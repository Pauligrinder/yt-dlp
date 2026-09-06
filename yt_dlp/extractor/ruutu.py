import json
import re

from .common import InfoExtractor
from ..utils import (
    ExtractorError,
    determine_ext,
    int_or_none,
    parse_iso8601,
    parse_resolution,
    str_or_none,
    traverse_obj,
    try_call,
    url_or_none,
)


class RuutuIE(InfoExtractor):
    _VALID_URL = r'''(?x)
                    https?://
                        (?:
                            (?:www\.)?(?:ruutu|supla)\.fi/(?:video|movie|supla|audio)/|
                            static\.nelonenmedia\.fi/player/misc/embed_player\.html\?.*?\bnid=
                        )
                        (?P<id>\d+)
                    '''
    _TESTS = [{
        'url': 'http://www.ruutu.fi/video/2057306',
        'md5': '12f3b0b64087547b3747e5fbd092907f',
        'info_dict': {
            'id': '2057306',
            'ext': 'mp4',
            'title': 'Superpesis: katso koko kausi Ruudussa',
            'description': 'md5:bfb7336df2a12dc21d18fa696c9f8f23',
            'thumbnail': r're:https?://.+',
            'duration': 40,
            'age_limit': 0,
            'timestamp': 1430990580,
            'upload_date': '20150507',
            'series': 'Superpesis',
            'series_id': '1379173',
        },
    }, {
        # episode with subtitles
        'url': 'https://www.ruutu.fi/video/3799520',
        'info_dict': {
            'id': '3799520',
            'ext': 'mp4',
            'title': 'Poliisit - 31 - Espoo',
            'description': 'md5:a7c71453c19d1b2940b73baf9ea002e1',
            'thumbnail': r're:https?://.+',
            'duration': 1337,
            'age_limit': 7,
            'timestamp': 1614256200,
            'upload_date': '20210225',
            'series': 'Poliisit',
            'series_id': '1380351',
            'season': 'Season 9',
            'season_number': 9,
            'episode': '31 - Espoo',
            'episode_number': 31,
            'channel': 'VOD',
            'channel_id': '48',
        },
        'params': {'skip_download': True},
    }, {
        # DRM protected
        'url': 'https://www.ruutu.fi/movie/100004592',
        'only_matching': True,
    }, {
        'url': 'http://www.supla.fi/audio/2231370',
        'only_matching': True,
    }, {
        'url': 'https://static.nelonenmedia.fi/player/misc/embed_player.html?nid=3618790',
        'only_matching': True,
    }]
    _WEBPAGE_TESTS = [{
        'url': 'https://www.is.fi/ulkomaat/art-2000012208431.html',
        'info_dict': {
            'id': '100323202',
            'ext': 'mp4',
            'title': 'Putin tapasi Kushnerin ja Witkoffin Moskovassa',
            'description': 'md5:21b7d0fecc6fdb0d12fa44cc60432a40',
            'thumbnail': r're:https?://.+',
            'duration': 30,
            'age_limit': 0,
            'timestamp': 1788680919,
            'upload_date': '20260906',
            'series': 'IS Uutiset',
            'series_id': '2731462',
            'categories': ['Dokkari'],
        },
    }]
    _API_BASE = 'https://mcc.nm-ovp.nelonenmedia.fi'

    @classmethod
    def _extract_embed_urls(cls, url, webpage):
        # nelonen.fi
        settings = try_call(
            lambda: json.loads(re.search(
                r'jQuery\.extend\(Drupal\.settings, ({.+?})\);', webpage).group(1), strict=False))
        if settings:
            video_id = traverse_obj(settings, (
                'mediaCrossbowSettings', 'file', 'field_crossbow_video_id', 'und', 0, 'value'))
            if video_id:
                return [f'http://www.ruutu.fi/video/{video_id}']
        # hs.fi and is.fi
        settings = try_call(
            lambda: json.loads(re.search(
                '(?s)<script[^>]+id=[\'"]__NEXT_DATA__[\'"][^>]*>([^<]+)</script>',
                webpage).group(1), strict=False))
        if settings:
            video_ids = set(traverse_obj(settings, (
                'props', 'pageProps', 'page', 'assetData', 'splitBody', ..., 'video', 'sourceId')) or [])
            if video_ids:
                return [f'http://www.ruutu.fi/video/{v}' for v in video_ids]
            video_id = traverse_obj(settings, (
                'props', 'pageProps', 'page', 'assetData', 'mainVideo', 'sourceId'))
            if video_id:
                return [f'http://www.ruutu.fi/video/{video_id}']

    def _extract_formats_and_subtitles(self, video_id, media):
        formats, subtitles, seen_urls = [], {}, set()
        for name, stream in traverse_obj(media, (
                'streamUrls', {dict.items}, lambda _, v: not v[1].get('withCredentials'))):
            stream_url = traverse_obj(stream, ('url', {url_or_none}))
            if not stream_url or stream_url in seen_urls:
                continue
            seen_urls.add(stream_url)

            ext = determine_ext(stream_url)
            if ext == 'm3u8':
                fmts, subs = self._extract_m3u8_formats_and_subtitles(
                    stream_url, video_id, 'mp4', m3u8_id=name, fatal=False)
            elif ext == 'mpd':
                fmts, subs = self._extract_mpd_formats_and_subtitles(
                    stream_url, video_id, mpd_id=name, fatal=False)
            else:
                fmt = {'format_id': name, 'url': stream_url}
                if name == 'http':
                    # The progressive stream is unavailable for most videos
                    fmt['preference'] = -10
                fmts, subs = [fmt], {}

            if name.startswith('audio'):
                for fmt in fmts:
                    fmt['vcodec'] = 'none'
            formats.extend(fmts)
            self._merge_subtitles(subs, target=subtitles)

        for sub in traverse_obj(media, ('subtitles', lambda _, v: url_or_none(v['url']))):
            subtitles.setdefault(sub.get('language') or 'fi', []).append({
                'url': sub['url'],
                'name': sub.get('name'),
            })

        return formats, subtitles

    def _extract_thumbnails(self, media):
        return [{
            'id': f'{name}_{resolution}',
            'url': url,
            **parse_resolution(resolution),
        } for name, images in traverse_obj(media, ('images', {dict.items}, lambda _, v: v[1]))
            for resolution, url in traverse_obj(images, ({dict.items}, lambda _, v: url_or_none(v[1])))]

    def _real_extract(self, url):
        video_id = self._match_id(url)

        video_json = self._download_json(
            f'{self._API_BASE}/v2/media/{video_id}', video_id, expected_status=(403, 404))
        if not video_json.get('clip'):
            raise ExtractorError(
                video_json.get('message') or 'Unable to extract video', expected=True)

        playback = traverse_obj(video_json, ('clip', 'playback', {dict})) or {}
        metadata = traverse_obj(video_json, ('clip', 'metadata', {dict})) or {}
        media = traverse_obj(playback, ('media', {dict})) or {}

        formats, subtitles = [], {}
        if video_json.get('success'):
            formats, subtitles = self._extract_formats_and_subtitles(video_id, media)
        elif traverse_obj(playback, ('drm', 'enabled')):
            # Unplayable videos are only served placeholder stream URLs
            self.report_drm(video_id)
        else:
            self.raise_login_required(
                'This video is only available for subscribers', metadata_available=True)

        return {
            'id': video_id,
            'formats': formats,
            'subtitles': subtitles,
            'thumbnails': self._extract_thumbnails(media),
            'duration': traverse_obj(playback, ('runtime', {int_or_none})),
            'age_limit': int_or_none(metadata.get('ageLimit')) or 0,
            'timestamp': min(traverse_obj(
                metadata, ('online_rights', ..., 'start_date', {parse_iso8601})), default=None),
            **traverse_obj(metadata, {
                'title': ('programName', {str}),
                'description': ('description', {str}),
                'channel': ('channelName', {str}, filter),
                'channel_id': ('channelId', {int_or_none}, filter, {str_or_none}),
                'series': ('seriesName', {str}),
                'series_id': ('seriesId', {str_or_none}),
                'season_number': ('seasonNumber', {int_or_none}),
                'episode': ('episodeName', {str}),
                'episode_number': ('episodeNumber', {int_or_none}),
            }),
            'categories': traverse_obj(video_json, (
                'clip', 'passthroughVariables', 'themes', {str}, filter, {lambda x: x.split(',')})),
        }
