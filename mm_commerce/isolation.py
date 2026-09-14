"""Per-line / per-candidate isolation — forbid cross-line contamination.

Every candidate MUST carry tender_item_id + unique candidate_id.
Evidence matrices are keyed by (tender_item_id, candidate_id).
Temp match state is cleared between items; fields must never leak across lines.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any


# Fields that must never be reused across tender lines
PROTECTED_FIELDS = frozenset(
    {
        "product_type",
        "brand",
        "model",
        "attributes",
        "stock",
        "price",
        "url",
        "evidence",
        "technical_status",
        "commercial_status",
        "match_pct",
        "candidate_title",
        "candidate_text",
        "source_url",
        "evidence_matrix",
        "product_type_need",
        "product_type_found",
    }
)


@dataclass
class CandidateRef:
    """Identity of one supplier candidate bound to exactly one tender line."""

    tender_item_id: int
    candidate_id: str
    line_no: int = 0

    def __post_init__(self) -> None:
        if self.tender_item_id is None:
            raise ValueError("item_id mandatory on every candidate")
        if not self.candidate_id:
            raise ValueError("candidate_id mandatory and unique")

    @property
    def matrix_key(self) -> str:
        return f"{self.tender_item_id}::{self.candidate_id}"


def make_candidate_id(
    *,
    tender_item_id: int,
    url: str = "",
    title: str = "",
    salt: str = "",
) -> str:
    """Stable unique id for a candidate within a line."""
    base = f"{tender_item_id}|{url.strip()}|{title.strip()}|{salt}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"c{tender_item_id}-{digest}"


def new_ephemeral_candidate_id(tender_item_id: int) -> str:
    return f"c{tender_item_id}-{uuid.uuid4().hex[:12]}"


@dataclass
class EvidenceStore:
    """Evidence matrices keyed by tender_item_id + candidate_id."""

    _by_key: dict[str, list[str]] = field(default_factory=dict)
    _meta: dict[str, dict[str, Any]] = field(default_factory=dict)

    def put(
        self,
        ref: CandidateRef,
        matrix_rows: list[str],
        *,
        meta: dict[str, Any] | None = None,
    ) -> None:
        key = ref.matrix_key
        # Always copy — never share list refs across candidates
        self._by_key[key] = list(matrix_rows)
        self._meta[key] = dict(meta or {})
        self._meta[key]["tender_item_id"] = ref.tender_item_id
        self._meta[key]["candidate_id"] = ref.candidate_id

    def get(self, ref: CandidateRef) -> list[str]:
        return list(self._by_key.get(ref.matrix_key, []))

    def keys_for_item(self, tender_item_id: int) -> list[str]:
        prefix = f"{tender_item_id}::"
        return [k for k in self._by_key if k.startswith(prefix)]

    def clear(self) -> None:
        self._by_key.clear()
        self._meta.clear()


@dataclass
class MatchSession:
    """Clears temp state between tender items; audits cross-line reuse."""

    evidence: EvidenceStore = field(default_factory=EvidenceStore)
    used_urls: set[str] = field(default_factory=set)
    used_fingerprints: set[str] = field(default_factory=set)
    _current_item_id: int | None = None
    _temp: dict[str, Any] = field(default_factory=dict)
    audit_log: list[str] = field(default_factory=list)

    def begin_item(self, tender_item_id: int, *, line_no: int = 0) -> None:
        """Mandatory clear of temp state between items."""
        if self._current_item_id is not None and self._current_item_id != tender_item_id:
            self.clear_temp()
        self._current_item_id = tender_item_id
        self._temp = {
            "tender_item_id": tender_item_id,
            "line_no": line_no,
            "classification": None,
            "product_type": None,
            "brand": None,
            "model": None,
            "attributes": None,
            "stock": None,
            "price": None,
            "url": None,
            "evidence": None,
        }

    def clear_temp(self) -> None:
        self._temp = {}

    def end_item(self) -> None:
        self.clear_temp()
        self._current_item_id = None

    def bind_candidate(
        self,
        *,
        tender_item_id: int,
        url: str = "",
        title: str = "",
        line_no: int = 0,
        salt: str = "",
    ) -> CandidateRef:
        if tender_item_id is None:
            raise ValueError("item_id mandatory on every candidate")
        if self._current_item_id != tender_item_id:
            self.begin_item(tender_item_id, line_no=line_no)
        cid = make_candidate_id(
            tender_item_id=tender_item_id, url=url, title=title, salt=salt
        )
        # Guarantee uniqueness even if URL/title collide
        while any(
            self.evidence._meta.get(k, {}).get("candidate_id") == cid
            for k in self.evidence._by_key
        ):
            cid = new_ephemeral_candidate_id(tender_item_id)
        return CandidateRef(
            tender_item_id=tender_item_id, candidate_id=cid, line_no=line_no
        )

    def remember_url(self, url: str) -> None:
        if url:
            self.used_urls.add(url.strip())

    def url_used(self, url: str) -> bool:
        return bool(url) and url.strip() in self.used_urls

    def fingerprint(
        self,
        *,
        product_type: str = "",
        brand: str = "",
        model: str = "",
        price: Any = None,
        stock: str = "",
        url: str = "",
        title: str = "",
    ) -> str:
        raw = f"{product_type}|{brand}|{model}|{price}|{stock}|{url}|{title}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def audit_candidate_payload(
        self,
        ref: CandidateRef,
        payload: dict[str, Any],
        *,
        other_item_payloads: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        """Detect accidental reuse of protected fields across lines."""
        issues: list[str] = []
        if payload.get("tender_item_id") not in (None, ref.tender_item_id):
            issues.append(
                f"ITEM_ID_MISMATCH:payload={payload.get('tender_item_id')} ref={ref.tender_item_id}"
            )
        if not payload.get("candidate_id"):
            issues.append("MISSING_CANDIDATE_ID")
        elif payload.get("candidate_id") != ref.candidate_id:
            issues.append("CANDIDATE_ID_MISMATCH")

        others = other_item_payloads or []
        for field_name in (
            "product_type_found",
            "brand",
            "model",
            "url",
            "price",
            "stock",
            "evidence_json",
            "candidate_title",
        ):
            val = payload.get(field_name)
            if val in (None, "", {}, []):
                continue
            for other in others:
                if other.get("tender_item_id") == ref.tender_item_id:
                    continue
                oval = other.get(field_name)
                if oval in (None, "", {}, []):
                    continue
                # Same concrete URL across different lines is forbidden
                if field_name == "url" and str(val).strip() == str(oval).strip():
                    issues.append(
                        f"CROSS_LINE_URL_REUSE:{val} "
                        f"item={ref.tender_item_id}<->{other.get('tender_item_id')}"
                    )
                # Identical evidence blob across different lines
                if field_name == "evidence_json" and val == oval:
                    issues.append(
                        f"CROSS_LINE_EVIDENCE_REUSE:"
                        f"item={ref.tender_item_id}<->{other.get('tender_item_id')}"
                    )
        for i in issues:
            self.audit_log.append(i)
        return issues


def assert_no_cross_contamination(
    line_results: list[dict[str, Any]],
) -> list[str]:
    """Audit a batch of per-line best results — R4 must not inherit R6 data, etc."""
    errors: list[str] = []
    by_item: dict[int, dict[str, Any]] = {}
    for row in line_results:
        tid = row.get("tender_item_id")
        if tid is None:
            errors.append("ROW_MISSING_ITEM_ID")
            continue
        if tid in by_item:
            errors.append(f"DUPLICATE_BEST_FOR_ITEM:{tid}")
        by_item[tid] = row

    items = list(by_item.items())
    for i, (tid_a, a) in enumerate(items):
        for tid_b, b in items[i + 1 :]:
            # Distinct lines must not share candidate_id
            ca, cb = a.get("candidate_id"), b.get("candidate_id")
            if ca and cb and ca == cb:
                errors.append(f"SHARED_CANDIDATE_ID:{ca} items={tid_a},{tid_b}")
            ua, ub = (a.get("url") or "").strip(), (b.get("url") or "").strip()
            if ua and ub and ua == ub:
                errors.append(f"SHARED_URL:{ua} items={tid_a},{tid_b}")
            # product_type_found identical AND title identical → suspicious reuse
            if (
                a.get("product_type_found")
                and a.get("product_type_found") == b.get("product_type_found")
                and a.get("candidate_title")
                and a.get("candidate_title") == b.get("candidate_title")
                and a.get("candidate_title")
            ):
                errors.append(
                    f"SHARED_TITLE_AND_TYPE:{a.get('candidate_title')!r} "
                    f"type={a.get('product_type_found')} items={tid_a},{tid_b}"
                )
            # Explicit: SPLITTER line must not show SOPORTE_NAP found from NAP line
            need_a = a.get("product_type_need")
            need_b = b.get("product_type_need")
            found_a = a.get("product_type_found")
            found_b = b.get("product_type_found")
            if need_a == "SPLITTER" and found_a == "SOPORTE_NAP" and need_b == "CAJA_NAP":
                errors.append(
                    f"R_SPLITTER_INHERITED_NAP_SUPPORT:items={tid_a}<->{tid_b}"
                )
            if need_b == "SPLITTER" and found_b == "SOPORTE_NAP" and need_a == "CAJA_NAP":
                errors.append(
                    f"R_SPLITTER_INHERITED_NAP_SUPPORT:items={tid_b}<->{tid_a}"
                )
    return errors
