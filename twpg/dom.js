() => {
  const tidy=s=>String(s||'').replace(/\s+/g,' ').trim();
  const pageUrl=u=>{try{let x=new URL(u,location.href);if(!['telewebion.net','www.telewebion.net'].includes(x.hostname))return '';
    return 'https://telewebion.net'+x.pathname.replace(/\/$/,'')+x.search;}catch{return '';}};
  const contentRe=/^\/(program|product|kids)\/(0x[0-9a-f]+)\/?$/i;
  const imgUrl=el=>{
    if(!el)return '';
    for(const val of [el.getAttribute('data-src'),el.getAttribute('data-original'),el.getAttribute('data-lazy-src'),el.getAttribute('src'),el.currentSrc]){
      if(!val)continue;
      const u=new URL(val,location.href);
      if(u.protocol==='https:'&&(['static.telewebion.net','telewebion.net','www.telewebion.net'].includes(u.hostname)))return u.href;
    } return '';
  };
  const headings=[...document.querySelectorAll('h1,h2')].map(h=>({text:tidy(h.textContent),y:h.getBoundingClientRect().top+window.scrollY})).filter(h=>h.text&&h.text.length<160);
  const section=a=>{
    let n=a;
    for(let i=0;n&&i<12;n=n.parentElement,i++){
      const name=n.getAttribute?.('data-row-title');if(name)return tidy(name);
      if(n.getAttribute?.('data-gtm')==='row'){
        let h=n.querySelector('[data-gtm="row-title"],h2');if(h){let t=tidy(h.getAttribute('data-title')||h.textContent);if(t)return t}
      }
    }
    const y=a.getBoundingClientRect().top+window.scrollY;
    let best=null;
    for(const h of headings)if(h.y<=y+25&&(!best||h.y>best.y))best=h;
    return best?.text||'';
  };
  const getPoster=a=>{
    let p=a;
    for(let i=0;p&&i<3;p=p.parentElement,i++){
      const im=p.querySelector('img');let url=imgUrl(im);
      if(url)return url;
      if(i>0&&p.querySelectorAll('a[href]').length>3)break;
    }return '';
  };
  const getTitle=a=>{
    const fallback=['', 'مشاهده جزئیات','مشاهده بیشتر','تماشا','بیشتر','مشاهده','ورود'];
    const im=a.querySelector('img');
    const own=[a.querySelector('h3,h4')?.textContent,a.getAttribute('data-tile'),a.getAttribute('title'),im?.getAttribute('alt'),a.textContent];
    let p=a;
    for(let i=0;p&&i<3;p=p.parentElement,i++){
      if(i){if(p.querySelectorAll('a[href]').length>4)break;
        own.push(p.querySelector('h3,h4')?.textContent,p.querySelector('img[alt]')?.getAttribute('alt'));
      }
    }
    for(const v of own){const t=tidy(v);if(t&&t.length<240&&!fallback.includes(t))return t}
    return '';
  };
  const found=[],topics=[],seen=new Set(),seenTopics=new Set();let episodeLinks=0;
  for(const a of document.querySelectorAll('a[href]')){
    const url=pageUrl(a.getAttribute('href'));if(!url)continue;
    const path=new URL(url).pathname;
    if(/^\/episode\/(0x[0-9a-f]+)\/?$/i.test(path)){episodeLinks++;continue;}
    const m=path.match(contentRe);
    if(m){const name=getTitle(a);if(!name)continue;
      const img=getPoster(a);const key=m[1].toLowerCase()+':'+m[2].toLowerCase();
      if(seen.has(key))continue;seen.add(key);
      const holder=a.closest('[data-index]');const index=holder&&/^\d+$/.test(holder.getAttribute('data-index')||'')?Number(holder.getAttribute('data-index')):null;
      found.push({url:'https://telewebion.net/'+m[1].toLowerCase()+'/'+m[2].toLowerCase(),name,image_url:img,section:section(a),index});
      continue;
    }
    if(/^\/(page\/[\w-]+|tags\/0x[0-9a-f]+|kids|space\/(MovieSeries|sport))\/?$/i.test(path)){
      let label=tidy(a.getAttribute('title')||a.textContent)||section(a);
      if(!seenTopics.has(url)){seenTopics.add(url);topics.push({url,label,section:section(a)})}
    }
  }
  const r=document.scrollingElement||document.documentElement;
  return {items:found,topics,episode_count:episodeLinks,
    title:document.title,body_excerpt:tidy(document.body?.innerText).slice(0,240),
    scroll_y:window.scrollY,scroll_height:Math.max(r.scrollHeight,document.body?.scrollHeight||0)};
}
