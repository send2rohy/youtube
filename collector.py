
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


def parse_duration(duration):
    """
    ISO 8601 영상 길이를 초 단위로 변환한다.

    예:
    PT1M30S -> 90
    PT45S   -> 45
    PT1H2M3S -> 3723
    """

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


def load_history():
    """
    history.json을 읽는다.

    새 구조:
    {
      "p": {
        "videoId": {
          "v": 조회수,
          "t": 수집시간
        }
      },
      "s": [
        {
          "t": 스냅샷 시간,
          "v": {
            "videoId": 조회수
          }
        }
      ]
    }

    기존 구형 구조도 자동으로 읽을 수 있도록 처리한다.
    """

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

    # --------------------------------------------------
    # 새 구조
    # --------------------------------------------------

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
                    )
                }

        if not isinstance(snapshots, list):
            snapshots = []

        return {
            "previous": previous,
            "snapshots": snapshots
        }

    # --------------------------------------------------
    # 기존 구형 구조
    #
    # {
    #   "videoId": {
    #       "viewCount": ...,
    #       "collectedAt": ...
    #   }
    # }
    # --------------------------------------------------

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
                )
            }

    return {
        "previous": previous,
        "snapshots": []
    }


def parse_iso_time(value):
    """
    ISO 시간을 datetime으로 변환한다.
    실패하면 None을 반환한다.
    """

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


def should_add_snapshot(
    snapshots,
    now
):
    """
    마지막 장기 스냅샷 이후
    6시간 이상 지났으면 새 스냅샷을 만든다.
    """

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


def build_snapshot(
    current_videos,
    now
):
    """
    장기 보관용 스냅샷.

    저장 용량을 줄이기 위해
    영상 ID와 조회수만 저장한다.
    """

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

    return {
        "t": now.isoformat(),
        "v": videos
    }


def save_history(
    previous,
    snapshots,
    current_videos,
    now
):
    """
    history.json 저장.

    p:
        바로 직전 실행 데이터.
        급상승 계산에 사용.

    s:
        6시간 단위 장기 스냅샷.
        최대 30일 보관.
    """

    # --------------------------------------------------
    # 바로 직전 실행 데이터
    #
    # 조회수와 시간만 저장한다.
    # --------------------------------------------------

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
            )
        }

    # --------------------------------------------------
    # 장기 스냅샷
    # --------------------------------------------------

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
                now
            )
        )

    # --------------------------------------------------
    # 최근 30일만 유지
    # --------------------------------------------------

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

    # 최신순이 아니라 시간순 유지
    cleaned_snapshots.sort(
        key=lambda x: x.get(
            "t",
            ""
        )
    )

    # --------------------------------------------------
    # 최종 저장
    # --------------------------------------------------

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


def get_candidate_videos():
    """
    YouTube의 현재 인기 후보를 가져온다.

    여기서는 mostPopular를
    '최종 급상승 순위'로 사용하지 않는다.

    단지 급상승 계산을 위한 후보군으로 사용한다.
    """

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

        # YouTube 공식 영상 카테고리 ID
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


def is_shorts(video):
    """
    Shorts 여부를 판정하기 위한 후보 분류.

    YouTube API에는 'isShorts'라는
    단순한 필드가 없으므로
    영상 길이를 이용해 우선 후보를 나눈다.

    실제 Shorts 판정은 YouTube 플랫폼의
    내부 기준과 완전히 동일하지 않을 수 있다.
    """

    duration = video.get(
        "durationSeconds",
        0
    )

    return duration <= 180


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


def calculate_score(
    current,
    previous
):
    """
    급상승 점수 계산.

    기존 계산 방식을 그대로 유지한다.
    """

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

    """
    조회수 증가량은 로그 스케일로 계산한다.

    이렇게 해야 초대형 채널이
    무조건 유리해지는 것을 어느 정도 줄일 수 있다.
    """

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


def main():

    print(
        "YouTube 급상승 데이터 수집 시작"
    )

    # --------------------------------------------------
    # 기존 history 읽기
    # --------------------------------------------------

    history = load_history()

    # --------------------------------------------------
    # 현재 시간
    # --------------------------------------------------

    now = datetime.now(
        timezone.utc
    )

    # --------------------------------------------------
    # YouTube 후보 영상 수집
    # --------------------------------------------------

    items = get_candidate_videos()

    print(
        "후보 영상:",
        len(items)
    )

    current_videos = collect_video_data(
        items
    )

    print(
        "수집 영상:",
        len(current_videos)
    )

    # --------------------------------------------------
    # 기존과 동일한 급상승 순위 계산
    # --------------------------------------------------

    normal, shorts = build_rankings(

        current_videos,

        history
    )

    # --------------------------------------------------
    # ranking.json 생성
    # --------------------------------------------------

    make_ranking_file(

        normal,

        shorts
    )

    # --------------------------------------------------
    # history.json 저장
    #
    # 직전 데이터 + 6시간 장기 스냅샷
    # --------------------------------------------------

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

        now
    )

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


if __name__ == "__main__":

    main()

