"""Preserve all JSON responses and index typed entities & connections.

Source tokens remain only in private PostgreSQL JSONB; never disclose in public API.
"""
from __future__ import annotations
import hashlib
import json
from typing import Any

IDENTIFIERS = {
    'ChannelID': 'channel', 'ProgramID': 'program', 'EpisodeID': 'episode',
    'TagID': 'tag', 'ContentID': 'content', 'content_id': 'content',
    'content_meta_id': 'content_meta', 'ContentMetaID': 'content_meta',
    'PageID': 'page', 'SpaceID': 'space', 'RowID': 'row',
    'ComponentID': 'component', 'SliderID': 'slider', 'BannerID': 'banner',
    'ChannelPlaylistID': 'playlist', 'ContentShowID':'content_show',
    'MediaID':'media', 'cast_id':'cast', 'role_id':'role',
    'StreamID':'stream',
}
TITLE_KEYS = ('title','persian_title','name_fa','name','special_title','english_title','title_en','label')


def unique_kind(obj: dict[str, Any]):
    # Different Kandoo variants use mixed casing for VOD and channels.
    for key, kind in IDENTIFIERS.items():
        v = obj.get(key)
        if isinstance(v, str) and v.startswith('0x'):
            return kind, v.lower()
    return None


def flatten_titles(obj):
    for k in TITLE_KEYS:
        v = obj.get(k)
        if isinstance(v,str) and v.strip():
            return v.strip()[:450]
    return ''


def source_kind(item: dict, ctx: str):
    if 'is_clip' in item and isinstance(item['is_clip'], bool):
        return item['is_clip']
    if '/queryProgram/' in ctx or '.queryProgram[' in ctx:
        return item.get('is_clip') if isinstance(item.get('is_clip'),bool) else None
    if '.getProgram.episodes[' in ctx:
        return False
    if 'getClipsByProgramID' in ctx:
        return True
    if 'getEpisodesByTagId' in ctx:
        return item.get('is_clip') if isinstance(item.get('is_clip'),bool) else None
    return None


def store_entity(db, kind: str, entity_id: str, obj: dict, ctx: str='', channel=None):
    title=flatten_titles(obj)
    ctype=obj.get('content_type') or obj.get('ContentType')
    image=obj.get('web_image_url') or obj.get('image_url') or obj.get('image')
    if not isinstance(image,str): image=None
    channel=channel or (obj.get('descriptor') if kind=='channel' else None)
    db.execute('''INSERT INTO entities(kind,entity_id,title,subtitle,content_type,channel_descriptor,
      is_clip,image_ref,raw,field_count) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
      ON CONFLICT (kind,entity_id) DO UPDATE SET
       title=CASE WHEN LENGTH(EXCLUDED.title)>0 THEN EXCLUDED.title ELSE entities.title END,
       subtitle=COALESCE(EXCLUDED.subtitle,entities.subtitle),
       content_type=COALESCE(EXCLUDED.content_type,entities.content_type),
       channel_descriptor=COALESCE(EXCLUDED.channel_descriptor,entities.channel_descriptor),
       is_clip=COALESCE(EXCLUDED.is_clip,entities.is_clip),
       image_ref=COALESCE(EXCLUDED.image_ref,entities.image_ref),
       raw=entities.raw || EXCLUDED.raw,
       field_count=GREATEST(entities.field_count,EXCLUDED.field_count),updated_at=now()''',
       (kind,entity_id,title,obj.get('title_en') or obj.get('english_title'),
        str(ctype)[:60] if ctype is not None else None,channel,
        source_kind(obj,ctx) if kind=='episode' else None,image,
        json.dumps(obj,ensure_ascii=False),len(obj)))


def index_document(db, payload: dict, *, task_type: str, channel: str|None=None):
    """Index every identified entity, even in deeply nested components.

    Complete original payload is always persisted in api_responses first.
    """
    seen=set(); counts={}; jobs=set()
    def walk(node, parent=None, relation='', path='$', depth=0):
        if depth>28 or len(seen)>180000:
            raise ValueError('excessive JSON structure')
        if isinstance(node,dict):
            ident=unique_kind(node)
            if ident:
                kind, entity_id=ident
                if (kind,entity_id,path) not in seen:
                    store_entity(db,kind,entity_id,node,path,channel if kind=='program' else None)
                    counts[kind]=counts.get(kind,0)+1
                    seen.add((kind,entity_id,path))
                if parent and ident != parent:
                    db.execute('''INSERT INTO links(parent_kind,parent_id,child_kind,child_id,relation)
                     VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',(*parent,*ident,relation[:100]))
                parent=ident
                if kind=='program' and entity_id not in jobs:
                    jobs.add(entity_id)
            for k,v in node.items():
                if isinstance(v,(dict,list)):
                    walk(v,parent,k,path+'.'+k,depth+1)
        elif isinstance(node,list):
            for i,value in enumerate(node):
                if isinstance(value,(dict,list)):
                    walk(value,parent,relation,path+f'[{i}]',depth+1)
    walk(payload)
    return counts, jobs


def doc_hash(payload):
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()
