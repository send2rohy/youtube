import os
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("YOUTUBE_API_KEY")

if not API_KEY:
    raise RuntimeError("YOUTUBE_API_KEY가 설정되어 있지 않습니다.")

API_URL = "https://www.googleapis.com/youtube/v3/videos"

REGION_CODE = "KR"

MAX_RESULTS = 50

HISTORY_FILE = "history.json"
RANKING_FILE = "ranking.json"

NORMAL_LIMIT = 20
SHORTS_LIMIT = 20

# 장기 이력 설정
SNAPSHOT_INTERVAL_HOURS = 6
HISTORY_DAYS = 30


# =========================================================
# YouTube API
# =========================================================

def youtube_request(params):
    params["key"] = API_KEY

    url = API_URL + "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "youtube-trending-collector/1.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read().decode("utf-8")

    result = json.loads(data)

    if "error" in result:
        message = result["error"].get(
            "message",
            "YouTube API 오류"
        )
        raise RuntimeError(message)

    return result


# =========================================================
# 영상 길이
# =========================================================

def parse_duration(duration):

    import re

    pattern = re.compile(
        r"PT"
        r"(?:(\d+)H)?"
        r"(?:(\d+)M)?"
        r"(?:(\d+)S)?"
    )

    match = pattern.fullmatch(duration)

    if not match:
        return 0

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)

    return (
        hours * 3600
        + minutes * 60
        + seconds
    )


# =========================================================
# history.json 읽기
# =========================================================

def load_history():

    if not os.path.exists(HISTORY_FILE):
        return {
            "previous": {},
            "snapshots": []
        }

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

    except Exception:

        return {
            "previous": {},
            "snapshots": []
        }

    # -----------------------------------------------------
    # 새 구조
    # -----------------------------------------------------

    if isinstance(data, dict) and (
        "p" in data or "s" in data
    ):

        previous_raw = data.get(
            "p",
            {}
        )

        snapshots = data.get(
            "s",
            []
        )

        previous = {}

        if isinstance(previous_raw, dict):

            for video_id, item in previous_raw.items():

                if not isinstance(item, dict):
                    continue

                previous[video_id] = {

                    "viewCount": int(
                        item.get(
                            "v",
                            0
                        )
                    ),

                    "collectedAt": item.get(
                        "t",
                        ""
                    ),

                    # 이전 순위
                    "rank": item.get(
                        "r",
                        None
                    ),

                    # 이전 타입
                    "type": item.get(
                        "y",
                        ""
                    )
                }

        if not isinstance(snapshots, list):
            snapshots = []

        return {
            "previous": previous,
            "snapshots": snapshots
        }

    # -----------------------------------------------------
    # 기존 구형 구조
    # -----------------------------------------------------

    previous = {}

    if isinstance(data, dict):

        for video_id, item in data.items():

            if not isinstance(item, dict):
                continue

            if "viewCount" not in item:
                continue

            previous[video_id] = {

                "viewCount": int(
                    item.get(
                        "viewCount",
                        0
                    )
                ),

                "collectedAt": item.get(
                    "collectedAt",
                    ""
                ),

                "rank": item.get(
                    "rank",
                    None
                ),

                "type": item.get(
                    "type",
                    ""
                )
            }

    return {
        "previous": previous,
        "snapshots": []
    }


# =========================================================
# ISO 시간
# =========================================================

def parse_iso_time(value):

    if not value:
        return None

    try:

        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00"
            )
        )

    except Exception:

        return None


# =========================================================
# 6시간 스냅샷 여부
# =========================================================

def should_add_snapshot(
    snapshots,
    now
):

    if not snapshots:
        return True

    last = snapshots[-1]

    if not isinstance(last, dict):
        return True

    last_time = parse_iso_time(
        last.get(
            "t",
            ""
        )
    )

    if not last_time:
        return True

    elapsed = (
        now - last_time
    ).total_seconds()

    return elapsed >= (
        SNAPSHOT_INTERVAL_HOURS
        * 3600
    )


# =========================================================
# 장기 스냅샷 생성
# =========================================================

def build_snapshot(
    current_videos,
    normal,
    shorts,
    now
):

    videos = {}

    for video_id, video in current_videos.items():

        try:

            videos[video_id] = int(
                video.get(
                    "viewCount",
                    0
                )
            )

        except Exception:

            videos[video_id] = 0

    # -----------------------------------------------------
    # 당시 순위 저장
    # -----------------------------------------------------

    normal_ranks = {}

    for index, video in enumerate(
        normal,
        start=1
    ):

        normal_ranks[
            video["videoId"]
        ] = index

    shorts_ranks = {}

    for index, video in enumerate(
        shorts,
        start=1
    ):

        shorts_ranks[
            video["videoId"]
        ] = index

    ranks = {}

    ranks.update(
        normal_ranks
    )

    ranks.update(
        shorts_ranks
    )

    return {

        "t": now.isoformat(),

        "v": videos,

        # 당시 순위
        "r": ranks
    }


# =========================================================
# history.json 저장
# =========================================================

def save_history(
    previous,
    snapshots,
    current_videos,
    normal,
    shorts,
    now
):

    # -----------------------------------------------------
    # 현재 순위 만들기
    # -----------------------------------------------------

    current_ranks = {}

    current_types = {}

    for index, video in enumerate(
        normal,
        start=1
    ):

        video_id = video["videoId"]

        current_ranks[video_id] = index

        current_types[video_id] = "video"

    for index, video in enumerate(
        shorts,
        start=1
    ):

        video_id = video["videoId"]

        current_ranks[video_id] = index

        current_types[video_id] = "shorts"

    # -----------------------------------------------------
    # 직전 실행 데이터
    #
    # 조회수
    # 수집시간
    # 직전 순위
    # 영상 타입
    # -----------------------------------------------------

    compact_previous = {}

    for video_id, video in current_videos.items():

        compact_previous[video_id] = {

            "v": int(
                video.get(
                    "viewCount",
                    0
                )
            ),

            "t": video.get(
                "collectedAt",
                now.isoformat()
            ),

            "r": current_ranks.get(
                video_id
            ),

            "y": current_types.get(
                video_id,
                ""
            )
        }

    # -----------------------------------------------------
    # 장기 스냅샷
    # -----------------------------------------------------

    snapshots = list(
        snapshots or []
    )

    if should_add_snapshot(
        snapshots,
        now
    ):

        snapshots.append(
            build_snapshot(
                current_videos,
                normal,
                shorts,
                now
            )
        )

    # -----------------------------------------------------
    # 최근 30일만 유지
    # -----------------------------------------------------

    cutoff = (
        now
        - timedelta(
            days=HISTORY_DAYS
        )
    )

    cleaned_snapshots = []

    for snapshot in snapshots:

        if not isinstance(
            snapshot,
            dict
        ):
            continue

        snapshot_time = parse_iso_time(
            snapshot.get(
                "t",
                ""
            )
        )

        if not snapshot_time:
            continue

        if snapshot_time >= cutoff:

            cleaned_snapshots.append(
                snapshot
            )

    # 시간순 정렬

    cleaned_snapshots.sort(
        key=lambda x: x.get(
            "t",
            ""
        )
    )

    # -----------------------------------------------------
    # 최종 저장
    # -----------------------------------------------------

    data = {

        "p": compact_previous,

        "s": cleaned_snapshots
    }

    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            separators=(
                ",",
                ":"
            )
        )


# =========================================================
# YouTube 인기 후보
# =========================================================

def get_candidate_videos():

    data = youtube_request({

        "part": (
            "snippet,"
            "contentDetails,"
            "statistics"
        ),

        "chart": "mostPopular",

        "regionCode": REGION_CODE,

        "maxResults": MAX_RESULTS,

        "hl": "ko"
    })

    return data.get(
        "items",
        []
    )


# =========================================================
# 영상 데이터 수집
# =========================================================

def collect_video_data(items):

    collected = {}

    now = datetime.now(
        timezone.utc
    )

    now_iso = now.isoformat()

    for item in items:

        video_id = item.get(
            "id"
        )

        if not video_id:
            continue

        snippet = item.get(
            "snippet",
            {}
        )

        statistics = item.get(
            "statistics",
            {}
        )

        content_details = item.get(
            "contentDetails",
            {}
        )

        title = snippet.get(
            "title",
            ""
        )

        channel_title = snippet.get(
            "channelTitle",
            ""
        )

        channel_id = snippet.get(
            "channelId",
            ""
        )

        published_at = snippet.get(
            "publishedAt",
            ""
        )

        category_id = snippet.get(
            "categoryId",
            ""
        )

        thumbnail = (
            snippet
            .get(
                "thumbnails",
                {}
            )
            .get(
                "high",
                {}
            )
            .get(
                "url",
                ""
            )
        )

        view_count = int(
            statistics.get(
                "viewCount",
                0
            )
        )

        like_count = int(
            statistics.get(
                "likeCount",
                0
            )
        )

        duration_iso = content_details.get(
            "duration",
            "PT0S"
        )

        duration_seconds = parse_duration(
            duration_iso
        )

        collected[video_id] = {

            "videoId": video_id,

            "title": title,

            "channelTitle": channel_title,

            "channelId": channel_id,

            "publishedAt": published_at,

            "categoryId": category_id,

            "thumbnail": thumbnail,

            "viewCount": view_count,

            "likeCount": like_count,

            "duration": duration_iso,

            "durationSeconds": duration_seconds,

            "collectedAt": now_iso
        }

    return collected


# =========================================================
# Shorts 판정
# =========================================================

def is_shorts(video):

    duration = video.get(
        "durationSeconds",
        0
    )

    return duration <= 180


# =========================================================
# 시간 계산
# =========================================================

def hours_between(
    old_time,
    new_time
):

    try:

        old_dt = datetime.fromisoformat(
            old_time.replace(
                "Z",
                "+00:00"
            )
        )

        new_dt = datetime.fromisoformat(
            new_time.replace(
                "Z",
                "+00:00"
            )
        )

        seconds = (
            new_dt - old_dt
        ).total_seconds()

        if seconds <= 0:
            return 0

        return seconds / 3600

    except Exception:

        return 0


# =========================================================
# 급상승 점수
# =========================================================

def calculate_score(
    current,
    previous
):

    if not previous:

        return {

            "viewIncrease": 0,

            "viewsPerHour": 0,

            "viewGrowthRate": 0,

            "score": 0
        }

    current_views = current[
        "viewCount"
    ]

    previous_views = previous.get(
        "viewCount",
        0
    )

    increase = (
        current_views
        - previous_views
    )

    if increase < 0:
        increase = 0

    elapsed_hours = hours_between(

        previous.get(
            "collectedAt",
            ""
        ),

        current.get(
            "collectedAt",
            ""
        )
    )

    if elapsed_hours <= 0:
        elapsed_hours = 1

    views_per_hour = (
        increase
        / elapsed_hours
    )

    if previous_views > 0:

        growth_rate = (
            increase
            / previous_views
        ) * 100

    else:

        growth_rate = 0

    volume_score = math.log10(
        1 + views_per_hour
    ) * 100

    growth_score = min(
        growth_rate,
        1000
    )

    recent_bonus = 0

    try:

        published = datetime.fromisoformat(

            current[
                "publishedAt"
            ].replace(
                "Z",
                "+00:00"
            )
        )

        now = datetime.now(
            timezone.utc
        )

        age_hours = (
            now - published
        ).total_seconds() / 3600

        if age_hours < 6:

            recent_bonus = 30

        elif age_hours < 12:

            recent_bonus = 20

        elif age_hours < 24:

            recent_bonus = 10

    except Exception:

        pass

    score = (

        volume_score * 0.65

        + min(
            growth_score,
            100
        ) * 0.25

        + recent_bonus
    )

    return {

        "viewIncrease": increase,

        "viewsPerHour": round(
            views_per_hour
        ),

        "viewGrowthRate": round(
            growth_rate,
            2
        ),

        "score": round(
            score,
            2
        )
    }


# =========================================================
# 순위 계산
# =========================================================

def build_rankings(
    current_videos,
    history
):

    normal = []

    shorts = []

    previous_history = history.get(
        "previous",
        {}
    )

    for video_id, current in current_videos.items():

        previous = previous_history.get(
            video_id
        )

        score_data = calculate_score(

            current,

            previous
        )

        result = dict(
            current
        )

        result.update(
            score_data
        )

        result["url"] = (

            "https://www.youtube.com/watch?v="

            + video_id
        )

        if is_shorts(current):

            result["type"] = "shorts"

            shorts.append(
                result
            )

        else:

            result["type"] = "video"

            normal.append(
                result
            )

    normal.sort(

        key=lambda x: x["score"],

        reverse=True
    )

    shorts.sort(

        key=lambda x: x["score"],

        reverse=True
    )

    return (

        normal[:NORMAL_LIMIT],

        shorts[:SHORTS_LIMIT]
    )


# =========================================================
# 순위 변동 계산
# =========================================================

def add_rank_changes(
    normal,
    shorts,
    previous
):

    previous = previous or {}

    # -----------------------------------------------------
    # 일반 영상
    # -----------------------------------------------------

    for index, video in enumerate(
        normal,
        start=1
    ):

        video_id = video["videoId"]

        old = previous.get(
            video_id,
            {}
        )

        old_rank = old.get(
            "rank"
        )

        video["rank"] = index

        if old_rank is None:

            video["rankChange"] = None

            video["rankStatus"] = "NEW"

        else:

            change = (
                old_rank - index
            )

            video["rankChange"] = change

            if change > 0:

                video["rankStatus"] = "UP"

            elif change < 0:

                video["rankStatus"] = "DOWN"

            else:

                video["rankStatus"] = "SAME"

    # -----------------------------------------------------
    # Shorts
    # -----------------------------------------------------

    for index, video in enumerate(
        shorts,
        start=1
    ):

        video_id = video["videoId"]

        old = previous.get(
            video_id,
            {}
        )

        old_rank = old.get(
            "rank"
        )

        video["rank"] = index

        if old_rank is None:

            video["rankChange"] = None

            video["rankStatus"] = "NEW"

        else:

            change = (
                old_rank - index
            )

            video["rankChange"] = change

            if change > 0:

                video["rankStatus"] = "UP"

            elif change < 0:

                video["rankStatus"] = "DOWN"

            else:

                video["rankStatus"] = "SAME"

    return normal, shorts


# =========================================================
# ranking.json
# =========================================================

def make_ranking_file(
    normal,
    shorts
):

    now = datetime.now(
        timezone.utc
    )

    data = {

        "updatedAt":
            now.isoformat(),

        "region":
            REGION_CODE,

        "description": (

            "YouTube API 수집 데이터를 기반으로 "

            "최근 조회수 증가 속도를 계산한 "

            "자체 급상승 순위"
        ),

        "normalVideos":
            normal,

        "shorts":
            shorts
    }

    with open(

        RANKING_FILE,

        "w",

        encoding="utf-8"

    ) as file:

        json.dump(

            data,

            file,

            ensure_ascii=False,

            indent=2
        )


# =========================================================
# 메인
# =========================================================

def main():

    print(
        "YouTube 급상승 데이터 수집 시작"
    )

    # -----------------------------------------------------
    # 기존 history
    # -----------------------------------------------------

    history = load_history()

    # -----------------------------------------------------
    # 현재 시간
    # -----------------------------------------------------

    now = datetime.now(
        timezone.utc
    )

    # -----------------------------------------------------
    # 후보 영상
    # -----------------------------------------------------

    items = get_candidate_videos()

    print(
        "후보 영상:",
        len(items)
    )

    # -----------------------------------------------------
    # 영상 데이터
    # -----------------------------------------------------

    current_videos = collect_video_data(
        items
    )

    print(
        "수집 영상:",
        len(current_videos)
    )

    # -----------------------------------------------------
    # 급상승 순위
    # -----------------------------------------------------

    normal, shorts = build_rankings(

        current_videos,

        history
    )

    # -----------------------------------------------------
    # 순위 변동 추가
    # -----------------------------------------------------

    normal, shorts = add_rank_changes(

        normal,

        shorts,

        history.get(
            "previous",
            {}
        )
    )

    # -----------------------------------------------------
    # ranking.json
    # -----------------------------------------------------

    make_ranking_file(

        normal,

        shorts
    )

    # -----------------------------------------------------
    # history.json
    # -----------------------------------------------------

    save_history(

        history.get(
            "previous",
            {}
        ),

        history.get(
            "snapshots",
            []
        ),

        current_videos,

        normal,

        shorts,

        now
    )

    # -----------------------------------------------------
    # 출력
    # -----------------------------------------------------

    print(
        "일반 동영상 순위:",
        len(normal)
    )

    print(
        "Shorts 순위:",
        len(shorts)
    )

    print(
        "ranking.json 생성 완료"
    )

    print(
        "history.json 장기 이력 저장 완료"
    )

    print(
        "순위 변동 데이터 저장 완료"
    )


if __name__ == "__main__":

    main()
