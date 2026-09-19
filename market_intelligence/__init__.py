"""Shared, bounded X research. No broker or trading authority.

Public methods never expose raw posts, credentials, or per-post author
identifiers. Single documented exception: the bounded source-account
registry lists numeric account ids of repeatedly relevant PUBLISHERS so the
user can verify their identity before activation (Zielbild Abschnitt 7 und
18.2); exports never map individual post texts to authors.
"""
from .service import (get_settings, save_settings, public_status, for_symbol,
                      event_hints, export_diagnostics, delete_evidence,
                      start_worker, stop_worker, tick, discovery_candidates, record_candidate_validation,
                      request_confirmation)

__all__ = ["get_settings", "save_settings", "public_status", "for_symbol",
           "event_hints", "export_diagnostics", "delete_evidence",
           "start_worker", "stop_worker", "tick", "discovery_candidates", "record_candidate_validation",
           "request_confirmation"]
