import os
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone

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

    if not os.path.exists(HISTORY_FILE):
        return {}

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception:

        return {}


def save_history(history):

    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            history,
            file,
            ensure_ascii=False,
            indent=2
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

    return data.get("items", [])


def collect_video_data(items):

    collected = {}

    now = datetime.now(timezone.utc)

    now_iso = now.isoformat()

    for item in items:

        video_id = item.get("id")

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
            .get("thumbnails", {})
            .get("high", {})
            .get("url", "")
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

            # 추가된 공식 YouTube 카테고리 ID
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

    핵심은 단순 총 조회수가 아니라
    최근 조회수 증가 속도를 중심으로 계산한다.
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

    for video_id, current in current_videos.items():

        previous = history.get(
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

    history = load_history()

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

    normal, shorts = build_rankings(

        current_videos,

        history
    )

    make_ranking_file(

        normal,

        shorts
    )

    save_history(

        current_videos
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


if __name__ == "__main__":

    main()
