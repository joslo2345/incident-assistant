from typing import Literal

from knowledge.search import Hit


def hit(
    cid: str,
    score: float = 1.0,
    kind: Literal["runbook", "incident"] = "runbook",
    text: str = "",
) -> Hit:
    doc = cid.split("#")[0]
    return Hit(
        cid,
        doc,
        kind,
        f"Title {doc}",
        "Heading",
        text or f"Title {doc} > Heading\n\nBody of {cid}.",
        score,
    )
