import os
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta


# ============================================================
# 설정
# ============================================================

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


# ============================================================
# 장기 추세 데이터 설정
# ============================================================

# 장기 그래프용 스냅샷은 6시간마다 저장
SNAPSHOT_INTERVAL_HOURS = 6

# 최대 30일 보관
HISTORY_DAYS = 30


# ============================================================
# 현재 시간
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


# ============================================================
# JSON 읽기
# ============================================================

def load_history():

    if not os.path.exists(HISTORY_FILE):
        return {
            "p": {},
            "s": []
        }

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

    except Exception:

        return {
            "p": {},
            "s": []
        }

    if not isinstance(data, dict):
        return {
            "p": {},
            "s": []
        }

    if "p" not in data:
        data["p"] = {}

    if "s" not in data:
        data["s"] = []

    if not isinstance(data["p"], dict):
        data["p"] = {}

    if not isinstance(data["s"], list):
        data["s"] = []

    return data


# ============================================================
# history 저장
# ============================================================

def save_history(history):

    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            history,
            f,
            ensure_ascii=False,
            separators=(",", ":")
        )


# ============================================================
# 이전 장기 스냅샷 확인
# ============================================================

def should_add_snapshot(history):

    snapshots = history.get("s", [])

    if not snapshots:
        return True

    latest = snapshots[-1]

    latest_time = latest.get("t")

    if not latest_time:
        return True

    try:

        latest_dt = datetime.fromisoformat(
            latest_time.replace(
                "Z",
                "+00:00"
            )
        )

    except Exception:

        return True

    elapsed = now_utc() - latest_dt

    return (
        elapsed.total_seconds()
        >=
        SNAPSHOT_INTERVAL_HOURS * 3600
    )


# ============================================================
# API 호출
# ============================================================

def get_candidate_videos():

    params = {
        "part": "snippet,contentDetails,statistics",
        "chart": "mostPopular",
        "regionCode": REGION_CODE,
        "maxResults": MAX_RESULTS,
        "hl": "ko",
        "key": API_KEY
    }

    url = (
        API_URL
        + "?"
        + urllib.parse.urlencode(params)
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent":
                "YouTubeRankingCollector/1.0"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        data = json.loads(
            response.read().decode("utf-8")
        )

    return data.get("items", [])


# ============================================================
# ISO 시간 파싱
# ============================================================

def parse_time(value):

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


# ============================================================
# 시간 차이
# ============================================================

def elapsed_hours(
    old_time,
    new_time
):

    old_dt = parse_time(old_time)

    new_dt = parse_time(new_time)

    if not old_dt or not new_dt:
        return 0.001

    seconds = (
        new_dt - old_dt
    ).total_seconds()

    return max(
        seconds / 3600,
        0.001
    )


# ============================================================
# ISO 8601 duration
# ============================================================

def parse_duration(duration):

    if not duration:
        return 0

    import re

    match = re.match(
        r"PT"
        r"(?:(\d+)H)?"
        r"(?:(\d+)M)?"
        r"(?:(\d+)S)?",
        duration
    )

    if not match:
        return 0

    hours = int(
        match.group(1) or 0
    )

    minutes = int(
        match.group(2) or 0
    )

    seconds = int(
        match.group(3) or 0
    )

    return (
        hours * 3600
        +
        minutes * 60
        +
        seconds
    )


# ============================================================
# Shorts 판정
# ============================================================

def is_shorts(duration_seconds):

    return duration_seconds <= 180


# ============================================================
# 숫자 변환
# ============================================================

def to_int(value):

    try:
        return int(value or 0)

    except Exception:
        return 0


# ============================================================
# 영상 데이터 수집
# ============================================================

def collect_video_data(
    items,
    history
):

    collected_at = iso_now()

    previous = history.get(
        "p",
        {}
    )

    videos = []

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

        view_count = to_int(
            statistics.get(
                "viewCount"
            )
        )

        like_count = to_int(
            statistics.get(
                "likeCount"
            )
        )

        duration = content_details.get(
            "duration",
            ""
        )

        duration_seconds = parse_duration(
            duration
        )

        published_at = snippet.get(
            "publishedAt"
        )

        previous_item = previous.get(
            video_id,
            {}
        )

        # ----------------------------------------------------
        # 이전 조회수
        # ----------------------------------------------------

        previous_views = to_int(
            previous_item.get(
                "v",
                view_count
            )
        )

        previous_time = previous_item.get(
            "t",
            collected_at
        )

        hours = elapsed_hours(
            previous_time,
            collected_at
        )

        # ----------------------------------------------------
        # 조회수 증가
        # ----------------------------------------------------

        increase = (
            view_count
            -
            previous_views
        )

        if increase < 0:
            increase = 0

        # ----------------------------------------------------
        # 시간당 증가
        # ----------------------------------------------------

        views_per_hour = (
            increase / hours
        )

        # ----------------------------------------------------
        # 증가율
        # ----------------------------------------------------

        if previous_views > 0:

            growth_rate = (
                increase
                /
                previous_views
                *
                100
            )

        else:

            growth_rate = 0

        # ----------------------------------------------------
        # 썸네일
        # ----------------------------------------------------

        thumbnails = snippet.get(
            "thumbnails",
            {}
        )

        thumbnail = (
            thumbnails
            .get("high", {})
            .get("url")
        )

        if not thumbnail:

            thumbnail = (
                thumbnails
                .get("medium", {})
                .get("url")
            )

        if not thumbnail:

            thumbnail = (
                thumbnails
                .get("default", {})
                .get("url", "")
            )

        # ----------------------------------------------------
        # 영상 데이터
        # ----------------------------------------------------

        videos.append({

            "videoId":
                video_id,

            "title":
                snippet.get(
                    "title",
                    ""
                ),

            "channelTitle":
                snippet.get(
                    "channelTitle",
                    ""
                ),

            "channelId":
                snippet.get(
                    "channelId",
                    ""
                ),

            "publishedAt":
                published_at,

            "categoryId":
                snippet.get(
                    "categoryId",
                    ""
                ),

            "thumbnail":
                thumbnail,

            "viewCount":
                view_count,

            "likeCount":
                like_count,

            "duration":
                duration,

            "durationSeconds":
                duration_seconds,

            "collectedAt":
                collected_at,

            "previousViewCount":
                previous_views,

            "viewIncrease":
                increase,

            "viewsPerHour":
                views_per_hour,

            "viewGrowthRate":
                growth_rate,

            "url":
                (
                    "https://www.youtube.com/watch?v="
                    +
                    video_id
                )
        })

    return videos


# ============================================================
# 트렌드 점수
# ============================================================

def calculate_score(video):

    views_per_hour = max(
        0,
        float(
            video.get(
                "viewsPerHour",
                0
            )
        )
    )

    growth_rate = max(
        0,
        float(
            video.get(
                "viewGrowthRate",
                0
            )
        )
    )

    # --------------------------------------------------------
    # 조회수 증가 속도 점수
    # --------------------------------------------------------

    volume_score = (
        math.log10(
            1 + views_per_hour
        )
        * 100
    )

    # --------------------------------------------------------
    # 증가율 점수
    # --------------------------------------------------------

    growth_score = min(
        growth_rate * 20,
        100
    )

    # --------------------------------------------------------
    # 최근 업로드 보너스
    # --------------------------------------------------------

    collected_at = parse_time(
        video.get(
            "collectedAt"
        )
    )

    published_at = parse_time(
        video.get(
            "publishedAt"
        )
    )

    recent_bonus = 0

    if collected_at and published_at:

        age_hours = (
            collected_at
            -
            published_at
        ).total_seconds() / 3600

        if age_hours < 6:

            recent_bonus = 30

        elif age_hours < 12:

            recent_bonus = 20

        elif age_hours < 24:

            recent_bonus = 10

    # --------------------------------------------------------
    # 최종 점수
    # --------------------------------------------------------

    score = (
        volume_score * 0.65
        +
        min(
            growth_score,
            100
        ) * 0.25
        +
        recent_bonus
    )

    return score


# ============================================================
# 순위 계산
# ============================================================

def build_rankings(
    videos,
    history
):

    # --------------------------------------------------------
    # 점수 계산
    # --------------------------------------------------------

    for video in videos:

        video["score"] = calculate_score(
            video
        )

    # --------------------------------------------------------
    # 일반 영상 / Shorts 분리
    # --------------------------------------------------------

    normal_videos = []

    shorts_videos = []

    for video in videos:

        if is_shorts(
            video.get(
                "durationSeconds",
                0
            )
        ):

            shorts_videos.append(
                video
            )

        else:

            normal_videos.append(
                video
            )

    # --------------------------------------------------------
    # 점수순 정렬
    # --------------------------------------------------------

    normal_videos.sort(
        key=lambda x: x.get(
            "score",
            0
        ),
        reverse=True
    )

    shorts_videos.sort(
        key=lambda x: x.get(
            "score",
            0
        ),
        reverse=True
    )

    previous = history.get(
        "p",
        {}
    )

    # --------------------------------------------------------
    # 순위 및 변동 처리
    #
    # 여기서는 50개 전체에 순위를 부여한다.
    # 그 후 화면 출력용 TOP20을 잘라낸다.
    # --------------------------------------------------------

    def apply_ranks(
        video_list
    ):

        for index, video in enumerate(
            video_list,
            start=1
        ):

            current_rank = index

            video["rank"] = current_rank

            previous_item = previous.get(
                video.get(
                    "videoId"
                ),
                {}
            )

            previous_rank = previous_item.get(
                "r"
            )

            # ------------------------------------------------
            # 과거 순위가 없는 경우
            # ------------------------------------------------

            if previous_rank is None:

                video["previousRank"] = None

                video["rankChange"] = None

                video["rankStatus"] = "NEW"

                continue

            # ------------------------------------------------
            # 숫자 변환
            # ------------------------------------------------

            try:

                previous_rank = int(
                    previous_rank
                )

            except Exception:

                previous_rank = None

            if previous_rank is None:

                video["previousRank"] = None

                video["rankChange"] = None

                video["rankStatus"] = "NEW"

                continue

            # ------------------------------------------------
            # 순위 변화
            # ------------------------------------------------

            change = (
                previous_rank
                -
                current_rank
            )

            video["previousRank"] = previous_rank

            video["rankChange"] = change

            if change > 0:

                video["rankStatus"] = "UP"

            elif change < 0:

                video["rankStatus"] = "DOWN"

            else:

                video["rankStatus"] = "SAME"

    # 전체 50개에 순위 부여
    apply_ranks(
        normal_videos
    )

    apply_ranks(
        shorts_videos
    )

    # --------------------------------------------------------
    # 화면에 보여줄 TOP20
    # --------------------------------------------------------

    normal_top = normal_videos[
        :NORMAL_LIMIT
    ]

    shorts_top = shorts_videos[
        :SHORTS_LIMIT
    ]

    return (
        normal_videos,
        shorts_videos,
        normal_top,
        shorts_top
    )


# ============================================================
# history 현재 데이터 갱신
# ============================================================

def update_previous_history(
    history,
    videos
):

    previous = {}

    for video in videos:

        video_id = video.get(
            "videoId"
        )

        if not video_id:
            continue

        previous_item = {

            "v":
                video.get(
                    "viewCount",
                    0
                ),

            "t":
                video.get(
                    "collectedAt"
                )
        }

        # 현재 순위가 계산되어 있으면 저장
        if video.get("rank") is not None:

            previous_item["r"] = video.get(
                "rank"
            )

        previous[video_id] = previous_item

    history["p"] = previous


# ============================================================
# 장기 스냅샷 생성
# ============================================================

def build_snapshot(
    videos
):

    view_snapshot = {}

    rank_snapshot = {}

    for video in videos:

        video_id = video.get(
            "videoId"
        )

        if not video_id:
            continue

        # 조회수
        view_snapshot[video_id] = video.get(
            "viewCount",
            0
        )

        # 순위
        if video.get("rank") is not None:

            rank_snapshot[video_id] = video.get(
                "rank"
            )

    return {

        "t":
            iso_now(),

        "v":
            view_snapshot,

        "r":
            rank_snapshot
    }


# ============================================================
# 오래된 스냅샷 삭제
# ============================================================

def cleanup_snapshots(
    history
):

    snapshots = history.get(
        "s",
        []
    )

    cutoff = (
        now_utc()
        -
        timedelta(
            days=HISTORY_DAYS
        )
    )

    cleaned = []

    for snapshot in snapshots:

        snapshot_time = parse_time(
            snapshot.get(
                "t"
            )
        )

        if not snapshot_time:
            continue

        if snapshot_time >= cutoff:

            cleaned.append(
                snapshot
            )

    history["s"] = cleaned


# ============================================================
# ranking.json 생성
# ============================================================

def make_ranking_file(
    normal_videos,
    shorts_videos
):

    return {

        "updatedAt":
            iso_now(),

        "region":
            REGION_CODE,

        "description":
            "대한민국 YouTube 인기 영상을 조회수 증가 속도와 증가율을 분석하여 계산한 트렌드 순위",

        "normalVideos":
            normal_videos,

        "shorts":
            shorts_videos
    }


# ============================================================
# ranking.json 저장
# ============================================================

def save_ranking(
    data
):

    with open(
        RANKING_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# 메인
# ============================================================

def main():

    print(
        "========================================"
    )

    print(
        "YouTube Ranking Collector"
    )

    print(
        "========================================"
    )

    # --------------------------------------------------------
    # 기존 history 불러오기
    # --------------------------------------------------------

    history = load_history()

    print(
        "기존 history 불러오기 완료"
    )

    # --------------------------------------------------------
    # YouTube API
    # --------------------------------------------------------

    print(
        "YouTube API 요청 중..."
    )

    items = get_candidate_videos()

    print(
        "API 영상 수:",
        len(items)
    )

    if not items:

        raise RuntimeError(
            "YouTube API에서 영상 데이터를 받지 못했습니다."
        )

    # --------------------------------------------------------
    # 영상 데이터 계산
    # --------------------------------------------------------

    videos = collect_video_data(
        items,
        history
    )

    print(
        "분석 영상 수:",
        len(videos)
    )

    # --------------------------------------------------------
    # 순위 계산
    # --------------------------------------------------------

    (
        normal_all,
        shorts_all,
        normal_top,
        shorts_top
    ) = build_rankings(
        videos,
        history
    )

    print(
        "일반 영상 전체:",
        len(normal_all)
    )

    print(
        "Shorts 전체:",
        len(shorts_all)
    )

    # --------------------------------------------------------
    # ranking.json
    # TOP20만 외부에 제공
    # --------------------------------------------------------

    ranking = make_ranking_file(
        normal_top,
        shorts_top
    )

    save_ranking(
        ranking
    )

    print(
        "ranking.json 저장 완료"
    )

    # --------------------------------------------------------
    # history의 현재 데이터 갱신
    #
    # 50개 전체 순위를 저장한다.
    # --------------------------------------------------------

    update_previous_history(
        history,
        normal_all + shorts_all
    )

    # --------------------------------------------------------
    # 장기 스냅샷
    # --------------------------------------------------------

    if should_add_snapshot(
        history
    ):

        snapshot = build_snapshot(
            normal_all + shorts_all
        )

        history.setdefault(
            "s",
            []
        ).append(
            snapshot
        )

        print(
            "장기 스냅샷 추가"
        )

    else:

        print(
            "6시간 이내이므로 장기 스냅샷 생략"
        )

    # --------------------------------------------------------
    # 30일 이전 스냅샷 삭제
    # --------------------------------------------------------

    cleanup_snapshots(
        history
    )

    # --------------------------------------------------------
    # history 저장
    # --------------------------------------------------------

    save_history(
        history
    )

    print(
        "history.json 저장 완료"
    )

    # --------------------------------------------------------
    # 결과 출력
    # --------------------------------------------------------

    print(
        "========================================"
    )

    print(
        "일반 영상 TOP 5"
    )

    for video in normal_top[:5]:

        change = video.get(
            "rankChange"
        )

        status = video.get(
            "rankStatus"
        )

        print(
            f"{video.get('rank')}위 | "
            f"{video.get('title', '')[:50]} | "
            f"점수 {video.get('score', 0):.2f} | "
            f"변동 {change} | "
            f"{status}"
        )

    print(
        "----------------------------------------"
    )

    print(
        "Shorts TOP 5"
    )

    for video in shorts_top[:5]:

        change = video.get(
            "rankChange"
        )

        status = video.get(
            "rankStatus"
        )

        print(
            f"{video.get('rank')}위 | "
            f"{video.get('title', '')[:50]} | "
            f"점수 {video.get('score', 0):.2f} | "
            f"변동 {change} | "
            f"{status}"
        )

    print(
        "========================================"
    )

    print(
        "수집 완료"
    )


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":
    main()
