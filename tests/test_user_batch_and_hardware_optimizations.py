import pytest
from pathlib import Path
from autoshorts.discovery.url_validator import parse_links_input
from autoshorts.cleanup.safety_cleaner import SafetyCleaner
from autoshorts.core.config import Settings
from autoshorts.core.models import UploadResult

USER_SAMPLE_INPUT = """
DIRECT YOUTUBE VIDEO LINKS

ISHOWSPEED https://www.youtube.com/watch?v=P4ekGt8eRJQ https://www.youtube.com/watch?v=nEFlyPH1lpI

KAI CENAT https://www.youtube.com/watch?v=BiLslrp276g

MRBEAST https://www.youtube.com/watch?v=Pv0iVoSZzN8

CASEOH https://www.youtube.com/watch?v=DSkPBsHSYgM

HAIX https://www.youtube.com/watch?v=iJgOjXoiX_g https://www.youtube.com/watch?v=683jITatS5E https://www.youtube.com/watch?v=iGBCZyijyvM https://www.youtube.com/watch?v=egz7NMLgF0A https://www.youtube.com/watch?v=nknNDtUhWLo https://www.youtube.com/watch?v=071SDI-yMwo https://www.youtube.com/watch?v=XAL__5wyxGQ https://www.youtube.com/watch?v=9fwC-8oFvcw https://www.youtube.com/watch?v=Z_gq3uhrep8 https://www.youtube.com/watch?v=WjtJCd-nOrs

DOROZEA https://www.youtube.com/watch?v=L1x1oR4JbEE

UFC https://www.youtube.com/watch?v=QCkACl1hn5A
HASHTAGS #shorts #youtubeshorts #viral #trending #fyp #viralshorts #gaming #gamingclips #streamer #streamclips #funny #memes #reaction #highlights #livestream #bestmoments #entertainment #shortsfeed #explore #contentcreator


https://www.youtube.com/watch?v=wYq3tCKuSBE : technoblade
https://www.youtube.com/watch?v=kBY8aLVOid0 : dorozea
https://www.youtube.com/watch?v=_Un-XumghSk : pokiest
https://www.youtube.com/watch?v=a_ZxRW-6Usw
https://www.youtube.com/watch?v=SCo6k5Ni5EQ : haix

from each video you can easily make more than 5+ clips. in few more than 20-30+ . so edit them and queue them and daily automate to post like 25-30 with all hastags and best settings so that highest payout and use /youtube skill for best practices. just churn. once youre done uploading all start reuploading but change title and description and hasttags just add more. and cycle until next links. just automate it completely. it should auto start and start uploading like 25-30 daily
"""


def test_user_batch_links_parsing():
    parsed = parse_links_input(USER_SAMPLE_INPUT)
    assert len(parsed) == 22

    # Check key video extractions and focus prompts
    speed_links = [p for p in parsed if p.video_id in ("P4ekGt8eRJQ", "nEFlyPH1lpI")]
    assert len(speed_links) == 2
    assert all(p.focus_prompt == "ISHOWSPEED" for p in speed_links)

    mrbeast = [p for p in parsed if p.video_id == "Pv0iVoSZzN8"]
    assert len(mrbeast) == 1
    assert mrbeast[0].focus_prompt == "MRBEAST"

    haix_links = [p for p in parsed if p.video_id in ("iJgOjXoiX_g", "WjtJCd-nOrs")]
    assert len(haix_links) == 2
    assert all(p.focus_prompt == "HAIX" for p in haix_links)

    techno = [p for p in parsed if p.video_id == "wYq3tCKuSBE"]
    assert len(techno) == 1
    assert techno[0].focus_prompt == "technoblade"


def test_post_upload_deletes_master_mp4(tmp_path: Path):
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    job_id = "job_test_upload_purge"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True)
    (job_work / "test_audio.wav").write_text("audio content", encoding="utf-8")

    master_mp4 = output_dir / f"{job_id}_master.mp4"
    master_mp4.write_text("mp4 video content", encoding="utf-8")
    preview_mp4 = output_dir / f"{job_id}_preview.mp4"
    preview_mp4.write_text("preview video content", encoding="utf-8")

    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        keep_master_mp4=False,
        use_recycle_bin=False,
        permanent=True,
    )

    upload_res = UploadResult(
        video_id="yt_12345",
        url="https://youtube.com/shorts/yt_12345",
        verified=True,
    )

    deleted = cleaner.cleanup_job(
        job_id=job_id,
        verified=True,
        upload_result=upload_res,
        keep_master_mp4=False,
    )

    # Master MP4, preview MP4, and work files MUST all be deleted from PC
    assert not master_mp4.exists(), "Master MP4 was not deleted after upload verification"
    assert not preview_mp4.exists(), "Preview MP4 was not deleted after upload verification"
    assert not (job_work / "test_audio.wav").exists(), "Work audio file was not deleted"
    assert len(deleted) >= 3
