# -*- coding: utf-8 -*-
"""Final synthetic case mirroring Translation Review / 44.zip failures."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.mt.cross_script_guard import has_phrase_loop  # noqa: E402
from engines.pipeline_language_gate import heal_phrase_loops_in_segments  # noqa: E402
from engines.tts_text_guard import (  # noqa: E402
    prepare_segment_text_for_tts,
    repair_neighbor_bleed,
)


def main() -> int:
    finals = [
        "18-річний хлопець на ім'я Джордж-молодший проїхав через своє рідне місто, повертаючись додому на вечерю.",
        "Але коли він їхав, Джордж-молодший не міг не відчути, що він справді боїться потрапити туди.",
        "Отже, Джордж-молодший був дуже розумною дитиною, але він також дуже легко відволікався, і через це він насправді не займався чимось настільки серйозно.",
        (
            "Тож за два тижні до того. у той момент, у той момент, у той момент, "
            "у той момент, у той момент, у той момент, у той момент, коли Джордж "
            "повертав. а потім щось трапилося"
        ),
    ]
    segs = []
    for i, t in enumerate(finals):
        segs.append(
            {
                "segment_id": str(i),
                "text": t,
                "plain_text": t,
                "final_text": t,
                "tts_text": t,
                "text_for_tts": t,
            }
        )
    segs.append(
        {
            "segment_id": "4",
            "text": "І ось Джордж підійшов до перехрестя.",
            "plain_text": "І ось Джордж підійшов до перехрестя.",
            "final_text": "І ось Джордж підійшов до перехрестя.",
            "tts_text": finals[0] + " І ось Джордж підійшов до перехрестя.",
        }
    )
    heal_phrase_loops_in_segments(
        segs, source_segments=[""] * len(segs), target_lang="uk", source_lang="en"
    )
    for s in segs:
        prepare_segment_text_for_tts(s)
    repair_neighbor_bleed(segs)

    assert "молодший відчути" not in segs[1]["tts_text"].lower(), segs[1]["tts_text"]
    assert not has_phrase_loop(segs[3]["tts_text"]), segs[3]["tts_text"]
    assert not segs[4]["tts_text"].startswith(finals[0][:30]), segs[4]["tts_text"]
    print("tts_review_synthetic OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
