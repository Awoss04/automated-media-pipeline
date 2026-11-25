"""Stage s04_rank — Kandidaten bewerten.

Ruft Antigravity (``agy``) mit einem JSON-Schema auf. Der Prompt kommt aus
``prompts/highlight_ranking.md``; das Transkript wird **als Daten**
uebergeben (in ``<<<TRANSKRIPT`` abgegrenzt), nie als Anweisung.

Gate: Antwort passt zum Schema, jeder Eintrag hat Score 0-10 und eine
Begruendung, Zeitstempel liegen im Video.
"""

from __future__ import annotations

import json

from media_pipeline.lib import agy, log
from media_pipeline.models import Candidate, RankedClip, StageResult


def _build_prompt(template: str, candidates: list[Candidate]) -> str:
    """Setzt die Platzhalter im Prompt mit den Kandidaten fuell."""
    payload = [
        {
            "id": c.id,
            "start_sec": c.start,
            "end_sec": c.end,
            "duration_sec": round(c.duration, 1),
            "transcript": c.transcript[:1500],
        }
        for c in candidates
    ]
    excerpt = "\n\n".join(
        f"[{c.id} {c.start:.0f}-{c.end:.0f}s] {c.transcript[:600]}"
        for c in candidates
        if c.transcript
    )[:12000]

    return (
        template.replace("{{CANDIDATES_JSON}}", json.dumps(payload, ensure_ascii=False, indent=2))
        .replace("{{TRANSCRIPT_EXCERPT}}", excerpt or "(kein Transkript vorhanden)")
    )


def run(ctx) -> StageResult:
    """Fuehrt s04_rank aus."""
    candidates = ctx.candidates()
    if not candidates:
        return StageResult(ok=False, error="Keine Kandidaten (s03_highlights fehlt)")

    prompt_file = ctx.prompts_dir / "highlight_ranking.md"
    if not prompt_file.is_file():
        return StageResult(ok=False, error=f"Prompt fehlt: {prompt_file}")

    schema_file = ctx.schemas_dir / "highlight_ranking.schema.json"
    if not schema_file.is_file():
        return StageResult(ok=False, error=f"JSON-Schema fehlt: {schema_file}")

    model = str(ctx.config.get("rank.model", agy.MODEL_RANK))
    timeout = int(ctx.config.get("rank.timeout_sec", 300))
    min_score = float(ctx.config.get("rank.min_score", 6.0))
    max_clips = int(ctx.config.get("rank.max_clips", 8))

    prompt = _build_prompt(prompt_file.read_text(encoding="utf-8"), candidates)
    log.info(f"Antigravity: bewerte {len(candidates)} Kandidaten (Modell {model})")

    try:
        response = agy.json_out(
            prompt=prompt, schema_path=schema_file, model=model, timeout=timeout
        )
    except agy.AgyError as exc:
        return StageResult(ok=False, error=f"Antigravity: {exc}")

    if not isinstance(response, dict):
        return StageResult(ok=False, error=f"Unerwartete Antwortform: {type(response).__name__}")

    rankings = response.get("rankings")
    if not isinstance(rankings, list) or not rankings:
        return StageResult(ok=False, error="Antwort enthaelt keine 'rankings'-Liste")

    by_id = {c.id: c for c in candidates}
    meta = ctx.video_meta()
    video_duration = float(meta.duration_sec) if meta else 0.0

    clips: list[RankedClip] = []
    problems: list[str] = []

    for entry in rankings:
        if not isinstance(entry, dict):
            problems.append("Eintrag ist kein Objekt")
            continue
        cand_id = str(entry.get("id", ""))
        candidate = by_id.get(cand_id)
        if candidate is None:
            problems.append(f"Unbekannte Kandidaten-ID: {cand_id}")
            continue

        try:
            score = float(entry.get("score"))
        except (TypeError, ValueError):
            problems.append(f"{cand_id}: Score nicht numerisch")
            continue

        if not 0.0 <= score <= 10.0:
            problems.append(f"{cand_id}: Score {score} ausserhalb 0-10")
            score = max(0.0, min(10.0, score))

        reason = str(entry.get("reason", "")).strip()
        if not reason:
            problems.append(f"{cand_id}: Begruendung fehlt")
            continue

        start = candidate.start
        end = candidate.end
        if video_duration > 0 and end > video_duration:
            end = video_duration
        if start >= end:
            problems.append(f"{cand_id}: ungueltiges Zeitfenster")
            continue

        title = str(entry.get("title", "")).strip()[:60]
        keywords = [str(k) for k in (entry.get("keywords") or [])][:6]
        best_moment = float(entry.get("best_moment_sec") or 0.0)
        best_moment = max(0.0, min(best_moment, end - start))

        clips.append(
            RankedClip(
                id=cand_id,
                start=start,
                end=end,
                score=score,
                reason=reason,
                title=title,
                keywords=keywords,
                best_moment_sec=best_moment,
            )
        )

    if not clips:
        return StageResult(
            ok=False,
            error="Kein gueltiger Kandidat in der Antwort" + (f": {'; '.join(problems[:3])}" if problems else ""),
        )

    # Nach Score sortieren, nur die besten behalten.
    clips.sort(key=lambda c: c.score, reverse=True)
    kept = [c for c in clips if c.score >= min_score][:max_clips]

    if not kept:
        return StageResult(
            ok=False,
            error=(
                f"Kein Kandidat erreicht den Mindestscore {min_score} "
                f"(bester: {clips[0].score:.1f})"
            ),
            data={"evaluated": len(clips)},
        )

    with ctx.ranked_path.open("w", encoding="utf-8") as fh:
        json.dump([c.to_dict() for c in kept], fh, indent=2, ensure_ascii=False)

    gate = (
        f"{len(clips)} bewertet, {len(kept)} ueber Score {min_score:.0f}, "
        f"bester {clips[0].score:.1f}"
    )
    if problems:
        log.warn(f"{len(problems)} Befunde: {'; '.join(problems[:3])}")
    return StageResult(
        ok=True,
        data={"evaluated": len(clips), "kept": len(kept), "best_score": clips[0].score},
        gate=gate,
    )
