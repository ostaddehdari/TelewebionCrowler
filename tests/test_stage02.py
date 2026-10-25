from datetime import datetime, timezone, timedelta
from twpg.discover_all import is_due, source_url
from twpg.channel_stats import CHANNEL_COUNTS_SQL

def test_channel_source_whitelist():
    record={'slug':'tv1','source_url':'https://telewebion.net/channel/tv1/programs'}
    assert source_url(record)==record['source_url']
    assert source_url({'slug':'fake','source_url':'http://telewebion.net/'}) is None
    assert source_url({'slug':'fake','source_url':'https://telewebion.net.evil.test/'}) is None
    assert source_url({'slug':'fake','source_url':'https://user@telewebion.net/'}) is None

def test_due_checkpoint():
    now=datetime(2026,10,25,tzinfo=timezone.utc)
    assert is_due(None,24,now)
    assert not is_due((now-timedelta(hours=2)).isoformat(),24,now)
    assert is_due((now-timedelta(hours=25)).isoformat(),24,now)
    assert is_due('invalid',24,now)

def test_aggregate_is_not_cross_product():
    assert 'jobs_by_channel' in CHANNEL_COUNTS_SQL
    assert 'media_by_channel' in CHANNEL_COUNTS_SQL
    assert "LEFT JOIN media_by_channel m" in CHANNEL_COUNTS_SQL
