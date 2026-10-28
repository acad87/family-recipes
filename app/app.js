import { createClient, html, render, useState, useEffect, useMemo, useRef, useCallback } from './vendor/lib.js';
import { SUPABASE_URL, SUPABASE_KEY, APP_VERSION } from './config.js';

const sb = createClient(SUPABASE_URL, SUPABASE_KEY, {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
});
const BUCKET = 'recipe-images';

// ---------------------------------------------------------------- helpers
const SECTION_FALLBACK = {
  packed_lunch: 'Packed lunches', family_dinner: 'Family dinners', kids_dinner: 'Kids’ dinners',
  breakfast: 'Breakfasts', snack: 'Snacks and lunchbox fillers', batch_freeze: 'Batch cook and freeze',
  quick: '20-minute weeknight', weekend: 'Weekend projects', cake_bake: 'Cakes and bakes', dessert: 'Desserts',
  basics: 'Sauces and basics', brazilian: 'Brazilian comfort food', baby: 'Baby and finger foods',
};
const LUNCH = { ambient: 'fine without an ice pack', ice_pack: 'needs an ice pack', no: 'not for the lunchbox' };
const RECIPE_COLS = 'id,status,title,description,source_name,source_url,sections,servings,batch_cook,slow_cook,hands_on_min,total_min,effort,cost,fridge_days,freezer_months,reheat,equipment,lunchbox,lunchbox_notes,toddler_ok,toddler_notes,hidden_veg,nutrients,allergens,nut_free,ingredients,steps,tips,images,ai_warnings,cover_image,cover_crop,sat_fat_g,heart_swaps,created_at,approved_at';

function fmtMin(min) {
  if (min == null) return '—';
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${h} h ${m}` : `${h} h`;
}
function fatInfo(g) {
  const n = g == null ? null : Number(g);
  if (n == null || Number.isNaN(n)) return { cls: 'fat-none', short: 'Sat fat ?', long: 'Saturated fat not estimated' };
  if (n <= 3) return { cls: 'fat-low', short: 'Low sat fat', long: `Low saturated fat · about ${n} g per serving` };
  if (n <= 6) return { cls: 'fat-mid', short: 'Medium sat fat', long: `Medium saturated fat · about ${n} g per serving` };
  return { cls: 'fat-high', short: 'High sat fat', long: `High saturated fat · about ${n} g per serving` };
}
function keepsText(r) {
  const fridge = r.fridge_days == null ? 'fridge: not given' : `fridge ${r.fridge_days} ${r.fridge_days === 1 ? 'day' : 'days'}`;
  const freezer = r.freezer_months == null ? 'freezer: not given' : r.freezer_months === 0 ? 'don’t freeze' : `freezer ${r.freezer_months} months`;
  return `${fridge} · ${freezer}`;
}
const go = (hash) => { location.hash = hash; };
function useHash() {
  const [hash, setHash] = useState(location.hash || '#/library');
  useEffect(() => {
    const on = () => { setHash(location.hash || '#/library'); window.scrollTo(0, 0); };
    addEventListener('hashchange', on);
    return () => removeEventListener('hashchange', on);
  }, []);
  return hash;
}

// signed picture links, cached for the session
const urlCache = new Map();
async function signPaths(paths) {
  const need = [...new Set(paths.filter((p) => p && !urlCache.has(p)))];
  if (!need.length) return;
  for (let i = 0; i < need.length; i += 100) {
    const { data, error } = await sb.storage.from(BUCKET).createSignedUrls(need.slice(i, i + 100), 60 * 60 * 6);
    if (error) { console.warn('sign', error); continue; }
    (data || []).forEach((d) => { if (d.signedUrl) urlCache.set(d.path, d.signedUrl); });
  }
}

// ---------------------------------------------------------------- smart search
const RULES = [
  { re: /^(lunch|lunchbox|lunchboxes|packed|school)$/, test: (r) => r.lunchbox === 'ambient' || r.lunchbox === 'ice_pack' || r.sections.includes('packed_lunch') },
  { re: /^(quick|fast|easy|weeknight)$/, test: (r) => r.total_min != null && r.total_min <= 30 },
  { re: /^(freeze|freezer|freezable|batch)$/, test: (r) => r.batch_cook || r.sections.includes('batch_freeze') || (r.freezer_months || 0) > 0 },
  { re: /^(slow|slowcooker|crockpot)$/, test: (r) => r.slow_cook || (r.equipment || []).includes('slow_cooker') },
  { re: /^(baby|tata|toddler)$/, test: (r) => r.toddler_ok === true },
  { re: /^(light|healthy|heart|lowfat)$/, test: (r) => r.sat_fat_g != null && Number(r.sat_fat_g) <= 3 },
  { re: /^(cheap|budget)$/, test: (r) => r.cost === '£' },
  { re: /^(veggie|vegetarian|meatfree)$/, test: (r) => !(r.ingredients || []).some((i) => i.category === 'meat' || i.category === 'fish') },
];
const STOP = new Set(['a', 'an', 'the', 'and', 'with', 'for', 'of', 'in', 'to', 'recipe', 'recipes', 'something', 'some', 'dinner', 'dinners', 'meal', 'meals', 'food']);
function makeMatcher(query) {
  const q = query.toLowerCase().replace(/[^\p{L}\p{N}\s£]/gu, ' ');
  const tests = [];
  const under = q.match(/(?:under|less than|max)\s*(\d+)/);
  let rest = q;
  if (under) { const n = Number(under[1]); tests.push((r) => r.total_min != null && r.total_min <= n); rest = rest.replace(under[0], ' '); }
  rest.split(/\s+/).filter(Boolean).forEach((w) => {
    if (STOP.has(w) || /^(min|mins|minutes)$/.test(w)) return;
    const rule = RULES.find((x) => x.re.test(w));
    if (rule) { tests.push(rule.test); return; }
    const stem = w.length > 4 ? w.replace(/(es|s)$/, '') : w;
    tests.push((r) => r._text.includes(stem));
  });
  return (r) => tests.every((t) => t(r));
}
function searchText(r, sectionLabel) {
  return [r.title, r.description, r.source_name, (r.ingredients || []).map((i) => `${i.item} ${i.display}`).join(' '),
    (r.sections || []).map(sectionLabel).join(' ')].join(' ').toLowerCase();
}

// ---------------------------------------------------------------- picture with crop
function Picture({ path, crop, className, label }) {
  const box = useRef(null);
  const [nat, setNat] = useState(null);
  const [size, setSize] = useState(null);
  const [url, setUrl] = useState(path ? urlCache.get(path) : null);
  useEffect(() => {
    let alive = true;
    if (path && !urlCache.get(path)) signPaths([path]).then(() => alive && setUrl(urlCache.get(path)));
    else setUrl(path ? urlCache.get(path) : null);
    return () => { alive = false; };
  }, [path]);
  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);
  let style = { left: '0', top: '0', width: '100%', height: '100%', objectFit: 'cover' };
  if (nat && size && size.w && size.h) {
    const c = crop || { left: 0, top: 0, right: 1, bottom: 1 };
    const cw = (c.right - c.left) * nat.w, ch = (c.bottom - c.top) * nat.h;
    const s = Math.max(size.w / cw, size.h / ch);
    style = {
      width: `${nat.w * s}px`, height: `${nat.h * s}px`,
      left: `${-c.left * nat.w * s + (size.w - cw * s) / 2}px`,
      top: `${-c.top * nat.h * s + (size.h - ch * s) / 2}px`,
    };
  }
  return html`<div class=${`pic ${className || ''}`} ref=${box}>
    ${url ? html`<img src=${url} alt=${label || ''} style=${style}
        onLoad=${(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />`
      : html`<div class="ph" aria-hidden="true">${(label || '?').slice(0, 1)}</div>`}
  </div>`;
}

// ---------------------------------------------------------------- icons
const Icon = ({ d }) => html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d=${d} /></svg>`;
const ICONS = {
  library: 'M4 19.5V5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2zM6 21h12M8 7h6',
  inbox: 'M3 13h5l1.5 3h5L16 13h5M5 5h14l2 8v6H3v-6z',
  add: 'M12 5v14M5 12h14',
  week: 'M7 3v3M17 3v3M4 8h16M5 5h14v15H5z',
};

// ---------------------------------------------------------------- app root
function App() {
  const [session, setSession] = useState(undefined);
  useEffect(() => {
    sb.auth.getSession().then(({ data }) => setSession(data.session || null));
    const { data: sub } = sb.auth.onAuthStateChange((_e, s) => setSession(s || null));
    return () => sub.subscription.unsubscribe();
  }, []);
  if (session === undefined) return html`<p class="boot">Opening the kitchen…</p>`;
  if (!session) return html`<${Login} />`;
  return html`<${Kitchen} session=${session} />`;
}

function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (!email.trim() || !password) { setError('Enter your email and password.'); return; }
    setBusy(true);
    const { error: err } = await sb.auth.signInWithPassword({ email: email.trim(), password });
    setBusy(false);
    if (err) setError(err.message === 'Invalid login credentials' ? 'That email and password don’t match.' : `Couldn’t log in: ${err.message}`);
  };
  return html`<main class="login">
    <h1 class="title">Our Kitchen</h1>
    <p class="subtle">Log in once on this device and it remembers you.</p>
    <form onSubmit=${submit} style="display:flex;flex-direction:column;gap:14px" novalidate>
      <label class="field">Email<input class="input" type="email" autocomplete="username" value=${email} onInput=${(e) => { setEmail(e.currentTarget.value); setError(''); }} /></label>
      <label class="field">Password<input class="input" type="password" autocomplete="current-password" value=${password} onInput=${(e) => { setPassword(e.currentTarget.value); setError(''); }} /></label>
      ${error && html`<p class="error" role="alert">${error}</p>`}
      <button class="btn primary big" type="submit" disabled=${busy}>${busy ? 'Logging in…' : 'Log in'}</button>
    </form>
  </main>`;
}

function Kitchen({ session }) {
  const hash = useHash();
  const [recipes, setRecipes] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [sections, setSections] = useState([]);
  const [me, setMe] = useState('');
  const [loadError, setLoadError] = useState('');
  const [toast, setToast] = useState('');
  const toastTimer = useRef(null);
  const say = useCallback((t) => { setToast(t); clearTimeout(toastTimer.current); toastTimer.current = setTimeout(() => setToast(''), 3500); }, []);

  const load = useCallback(async () => {
    const [r, j, t, m] = await Promise.all([
      sb.from('recipes').select(RECIPE_COLS).neq('status', 'archived').order('created_at', { ascending: false }),
      sb.from('import_jobs').select('id,source,status,error,attempts,images,texts,created_at,updated_at')
        .in('status', ['pending', 'processing', 'error']).order('created_at', { ascending: false }).limit(50),
      sb.from('tags').select('kind,slug,label,sort').eq('kind', 'section').order('sort'),
      sb.from('members').select('name').eq('user_id', session.user.id).maybeSingle(),
    ]);
    if (r.error) { setLoadError(r.error.message); return; }
    if (!m.data) { setLoadError('This login isn’t on the family list yet.'); return; }
    setLoadError('');
    setRecipes(r.data || []);
    setJobs(j.data || []);
    setSections(t.data || []);
    setMe(m.data.name);
    signPaths((r.data || []).map((x) => x.cover_image).filter(Boolean));
  }, [session.user.id]);

  useEffect(() => { load(); }, [load]);
  // live updates from the other iPad / the importer, plus a gentle poll as backup
  useEffect(() => {
    let t = null;
    const soon = () => { clearTimeout(t); t = setTimeout(load, 400); };
    const ch = sb.channel('kitchen')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'recipes' }, soon)
      .on('postgres_changes', { event: '*', schema: 'public', table: 'import_jobs' }, soon)
      .subscribe();
    const poll = setInterval(() => { if (document.visibilityState === 'visible') load(); }, 60000);
    const vis = () => { if (document.visibilityState === 'visible') load(); };
    document.addEventListener('visibilitychange', vis);
    return () => { clearTimeout(t); clearInterval(poll); document.removeEventListener('visibilitychange', vis); sb.removeChannel(ch); };
  }, [load]);

  const sectionLabel = useCallback((slug) => (sections.find((s) => s.slug === slug) || {}).label || SECTION_FALLBACK[slug] || slug, [sections]);
  const all = useMemo(() => (recipes || []).map((r) => ({ ...r, sections: r.sections || [], _text: searchText(r, sectionLabel) })), [recipes, sectionLabel]);
  const inbox = all.filter((r) => r.status === 'inbox');
  const library = all.filter((r) => r.status === 'approved');
  const busyJobs = jobs.filter((j) => j.status === 'pending' || j.status === 'processing');
  const failedJobs = jobs.filter((j) => j.status === 'error');

  const ctx = { all, inbox, library, jobs, busyJobs, failedJobs, sectionLabel, sections, load, say, me };

  if (loadError) return html`<main class="page"><h1 class="title">Our Kitchen</h1><p class="error" role="alert">${loadError}</p>
    <div class="row"><button class="btn" onClick=${load}>Try again</button><button class="btn" onClick=${() => sb.auth.signOut()}>Log out</button></div></main>`;
  if (!recipes) return html`<p class="boot">Fetching recipes…</p>`;

  const [, route, id] = hash.replace(/^#/, '').split('/');
  let view;
  if (route === 'recipe') view = html`<${RecipePage} ctx=${ctx} id=${id} />`;
  else if (route === 'cook') view = html`<${CookMode} ctx=${ctx} id=${id} />`;
  else if (route === 'edit') view = html`<${EditPage} ctx=${ctx} id=${id} />`;
  else if (route === 'inbox') view = html`<${InboxPage} ctx=${ctx} id=${id} />`;
  else if (route === 'add') view = html`<${AddPage} ctx=${ctx} />`;
  else if (route === 'week') view = html`<${WeekPage} ctx=${ctx} />`;
  else view = html`<${LibraryPage} ctx=${ctx} />`;

  const tab = route === 'recipe' || route === 'cook' || route === 'edit' ? (all.find((r) => r.id === id)?.status === 'inbox' ? 'inbox' : 'library') : (route || 'library');
  const tabs = [['library', 'Library', ''], ['inbox', 'Inbox', inbox.length ? String(inbox.length) : ''], ['add', 'Add', ''], ['week', 'This week', '']];
  return html`
    ${view}
    ${toast && html`<div class="notice" role="status" style="position:fixed;left:16px;right:16px;bottom:calc(var(--tabbar) + env(safe-area-inset-bottom) + 12px);z-index:40;max-width:560px;margin:0 auto">${toast}</div>`}
    ${route !== 'cook' && html`<nav class="tabbar" aria-label="Main"><div class="tabbar-inner">
      ${tabs.map(([key, label, badge]) => html`<a class="tab" href=${`#/${key}`} aria-current=${tab === key ? 'page' : undefined}>
        <${Icon} d=${ICONS[key]} /><span>${label}</span>${badge && html`<span class="badge" aria-label=${`${badge} waiting`}>${badge}</span>`}</a>`)}
    </div></nav>`}`;
}

// ---------------------------------------------------------------- library
const EXTRA_FILTERS = [
  ['tata', 'Good for Tata', (r) => r.toddler_ok === true],
  ['lunch', 'Lunchbox OK', (r) => r.lunchbox === 'ambient' || r.lunchbox === 'ice_pack'],
  ['lowfat', 'Low sat fat', (r) => r.sat_fat_g != null && Number(r.sat_fat_g) <= 3],
  ['freezes', 'Freezes', (r) => (r.freezer_months || 0) > 0],
  ['slow', 'Slow cooker', (r) => r.slow_cook || (r.equipment || []).includes('slow_cooker')],
];
function loadPrefs() { try { return JSON.parse(localStorage.getItem('library-prefs') || '{}'); } catch (e) { return {}; } }
function savePrefs(p) { try { localStorage.setItem('library-prefs', JSON.stringify(p)); } catch (e) { /* private mode */ } }

function LibraryPage({ ctx }) {
  const [prefs, setPrefs] = useState(() => ({ query: '', on: [], sort: 'new', ...loadPrefs() }));
  useEffect(() => savePrefs(prefs), [prefs]);
  const toggle = (k) => setPrefs({ ...prefs, on: prefs.on.includes(k) ? prefs.on.filter((x) => x !== k) : prefs.on.concat(k) });
  const usedSections = ctx.sections.filter((s) => ctx.library.some((r) => r.sections.includes(s.slug)));
  const match = makeMatcher(prefs.query);
  let list = ctx.library.filter((r) => prefs.on.every((k) => {
    const extra = EXTRA_FILTERS.find((f) => f[0] === k);
    return extra ? extra[2](r) : r.sections.includes(k);
  }) && match(r));
  if (prefs.sort === 'az') list = [...list].sort((a, b) => a.title.localeCompare(b.title));
  if (prefs.sort === 'quick') list = [...list].sort((a, b) => (a.total_min ?? 9999) - (b.total_min ?? 9999));

  return html`<main class="page">
    <div class="page-head"><h1 class="title">Recipes</h1><span class="subtle">${ctx.library.length} in the library</span></div>
    <label class="field"><span class="sr-only">Search</span>
      <input class="input" type="search" placeholder="e.g. quick chicken for the lunchbox" value=${prefs.query}
        onInput=${(e) => setPrefs({ ...prefs, query: e.currentTarget.value })} />
    </label>
    <div class="chips" role="group" aria-label="Filters">
      ${EXTRA_FILTERS.map(([k, label]) => html`<button class="chip" aria-pressed=${prefs.on.includes(k) ? 'true' : 'false'} onClick=${() => toggle(k)}>${label}</button>`)}
      ${usedSections.map((s) => html`<button class="chip" aria-pressed=${prefs.on.includes(s.slug) ? 'true' : 'false'} onClick=${() => toggle(s.slug)}>${s.label}</button>`)}
    </div>
    <div class="row spread">
      <span class="subtle">${list.length === ctx.library.length ? 'Showing all' : `${list.length} match${list.length === 1 ? '' : 'es'}`}</span>
      <label class="row subtle">Sort
        <select class="select" style="min-height:40px;width:auto" value=${prefs.sort} onChange=${(e) => setPrefs({ ...prefs, sort: e.currentTarget.value })}>
          <option value="new">Newest</option><option value="az">A–Z</option><option value="quick">Quickest</option>
        </select></label>
    </div>
    ${ctx.library.length === 0 ? html`<p class="empty">No approved recipes yet. New imports wait in the <a href="#/inbox">Inbox</a> for you to check and approve.</p>`
      : list.length === 0 ? html`<p class="empty">Nothing matches. Try fewer words or tap a highlighted filter to turn it off.</p>`
      : html`<div class="grid">${list.map((r) => html`<${Card} r=${r} />`)}</div>`}
  </main>`;
}

function Card({ r }) {
  const fat = fatInfo(r.sat_fat_g);
  const tags = [];
  if (r.batch_cook) tags.push(`Batch · ${r.servings}`);
  if (r.slow_cook) tags.push('Slow cooker');
  if (r.toddler_ok) tags.push('Tata OK');
  if (r.lunchbox === 'ambient' || r.lunchbox === 'ice_pack') tags.push('Lunchbox');
  return html`<a class="card" href=${`#/recipe/${r.id}`}>
    <${Picture} path=${r.cover_image} crop=${r.cover_crop} className="pic-card" label=${r.title} />
    <div class="card-body">
      <div class="card-title">${r.title}</div>
      <div class="subtle">${fmtMin(r.total_min)} · serves ${r.servings ?? '?'}${r.cost ? ` · ${r.cost}` : ''}</div>
      <div class="tags"><span class=${`tag ${fat.cls}`}>${fat.short}</span>${tags.map((t) => html`<span class="tag">${t}</span>`)}</div>
    </div>
  </a>`;
}

// ---------------------------------------------------------------- recipe page
function RecipeBody({ r, ctx }) {
  const fat = fatInfo(r.sat_fat_g);
  const groups = [];
  (r.ingredients || []).forEach((i) => {
    const name = i.group || '';
    let g = groups.find((x) => x.name === name);
    if (!g) { g = { name, items: [] }; groups.push(g); }
    g.items.push(i);
  });
  const stats = [['Hands-on', fmtMin(r.hands_on_min)], ['Total', fmtMin(r.total_min)], ['Serves', r.servings ?? '?'],
    ['Cost · effort', [r.cost, r.effort === 'weekend' ? 'Weekend' : r.effort === 'weeknight' ? 'Weeknight' : ''].filter(Boolean).join(' · ') || '—']];
  return html`
    <${Picture} path=${r.cover_image} crop=${r.cover_crop} className="pic-hero" label=${r.title} />
    <div style="display:flex;flex-direction:column;gap:8px">
      <h1 class="title">${r.title}</h1>
      ${r.description && html`<p style="margin:0;color:var(--ink-soft)">${r.description}</p>`}
      ${(r.sections || []).length > 0 && html`<div class="tags">${r.sections.map((s) => html`<span class="tag">${ctx.sectionLabel(s)}</span>`)}</div>`}
    </div>
    <div class="stats">${stats.map(([l, v]) => html`<div class="stat"><div class="stat-label">${l}</div><div class="stat-value">${v}</div></div>`)}</div>
    <div class="row"><span class="subtle" style="font-weight:600">Contains:</span>
      ${(r.allergens || []).length ? r.allergens.map((a) => html`<span class="tag allergen">${a.replace('_', ' ').replace(/^./, (c) => c.toUpperCase())}</span>`)
        : html`<span class="subtle">none of the 14 allergens found</span>`}
    </div>
    <div class="boxes">
      <section class="box box-tata"><h2>For Tata · ${r.toddler_ok === true ? 'OK with tweaks' : r.toddler_ok === false ? 'make a separate portion' : 'not checked'}</h2><p>${r.toddler_notes || 'No notes.'}</p></section>
      <section class="box box-lunch"><h2>Sisi’s lunchbox · ${LUNCH[r.lunchbox] || 'not checked'}</h2><p>${r.lunchbox_notes || 'No notes.'}</p></section>
      <section class=${`box ${fat.cls}`}><h2>${fat.long}</h2><p>${r.heart_swaps || 'Already low — no swaps needed.'}</p></section>
      <section class="box box-keep"><h2>Keeps · ${keepsText(r)}</h2><p>${r.tips || 'No tips.'}</p></section>
    </div>
    ${(r.ai_warnings || []).length > 0 && html`<details class="check"><summary>Check before cooking (${r.ai_warnings.length})</summary>
      <ul>${r.ai_warnings.map((w) => html`<li>${w}</li>`)}</ul></details>`}
    <div class="two-col">
      <section><h2 class="section-title">Ingredients</h2>
        ${groups.map((g) => html`${g.name && html`<h3 class="group-name">${g.name}</h3>`}
          <ul class="ingredients">${g.items.map((i) => html`<li>${i.display || i.item}${i.optional ? html` <span class="subtle">(optional)</span>` : ''}</li>`)}</ul>`)}
      </section>
      <section><h2 class="section-title">Method</h2>
        <ol class="steps">${(r.steps || []).map((s, i) => html`<li><span class="step-n" aria-hidden="true">${i + 1}</span><span><span class="sr-only">Step ${i + 1}: </span>${s}</span></li>`)}</ol>
      </section>
    </div>
    ${r.source_url && html`<p style="margin:0"><a href=${r.source_url} target="_blank" rel="noopener noreferrer">Open the original post${r.source_name ? ` by ${r.source_name}` : ''} ↗</a></p>`}`;
}

function RecipePage({ ctx, id }) {
  const r = ctx.all.find((x) => x.id === id);
  if (!r) return html`<main class="page"><p class="empty">That recipe isn’t here any more. <a href="#/library">Back to the library</a></p></main>`;
  return html`<main class="page">
    <div class="row spread">
      <a class="btn" href=${r.status === 'inbox' ? '#/inbox' : '#/library'}>← ${r.status === 'inbox' ? 'Inbox' : 'Recipes'}</a>
      <div class="row">
        <a class="btn" href=${`#/edit/${r.id}`}>Edit</a>
        <a class="btn primary" href=${`#/cook/${r.id}`}>Start cooking</a>
      </div>
    </div>
    <${RecipeBody} r=${r} ctx=${ctx} />
  </main>`;
}

// ---------------------------------------------------------------- cook mode
function CookMode({ ctx, id }) {
  const r = ctx.all.find((x) => x.id === id);
  const [i, setI] = useState(0);
  const [lockNote, setLockNote] = useState('');
  useEffect(() => {
    let lock = null, alive = true;
    const ask = async () => {
      try { if ('wakeLock' in navigator && document.visibilityState === 'visible') lock = await navigator.wakeLock.request('screen'); }
      catch (e) { if (alive) setLockNote('Tip: the screen may dim while cooking on this device.'); }
    };
    ask();
    const vis = () => { if (document.visibilityState === 'visible') ask(); };
    document.addEventListener('visibilitychange', vis);
    return () => { alive = false; document.removeEventListener('visibilitychange', vis); try { lock && lock.release(); } catch (e) { /* already released */ } };
  }, []);
  if (!r) return html`<main class="page"><p class="empty">Recipe not found. <a href="#/library">Back</a></p></main>`;
  const steps = r.steps && r.steps.length ? r.steps : ['No method was found for this recipe. Open the original post.'];
  const last = i >= steps.length - 1;
  return html`<div class="cook" role="dialog" aria-label=${`Cooking ${r.title}`}>
    <div class="cook-top"><span>${r.title} · step ${i + 1} of ${steps.length}</span>
      <button class="cook-btn" style="min-height:48px;padding:0 18px;font-size:16px" onClick=${() => go(`#/recipe/${r.id}`)}>Close</button></div>
    <p class="cook-step" aria-live="polite">${steps[i]}</p>
    <details class="cook-ing"><summary>Ingredients</summary>${(r.ingredients || []).map((x) => html`<div style="padding:5px 0;border-bottom:1px dashed #5A4833">${x.display || x.item}</div>`)}</details>
    <div class="cook-btns">
      <button class="cook-btn" onClick=${() => setI(Math.max(0, i - 1))} disabled=${i === 0}>← Back</button>
      <button class="cook-btn next" onClick=${() => (last ? go(`#/recipe/${r.id}`) : setI(i + 1))}>${last ? 'Finished' : 'Next step →'}</button>
    </div>
    ${lockNote && html`<div style="font-size:13px;color:#BFAE8E">${lockNote}</div>`}
  </div>`;
}

// ---------------------------------------------------------------- inbox
function InboxPage({ ctx, id }) {
  const current = ctx.inbox.find((r) => r.id === id) || ctx.inbox[ctx.inbox.length - 1];
  const [checked, setChecked] = useState({});
  const [busy, setBusy] = useState(false);
  const [confirmAll, setConfirmAll] = useState(false);
  useEffect(() => setChecked({}), [current && current.id]);

  const nextAfter = (rid) => { const rest = ctx.inbox.filter((r) => r.id !== rid); return rest.length ? rest[rest.length - 1].id : ''; };
  const approve = async () => {
    setBusy(true);
    const { error } = await sb.from('recipes').update({ status: 'approved', approved_at: new Date().toISOString() }).eq('id', current.id);
    setBusy(false);
    if (error) { ctx.say(`Couldn’t approve: ${error.message}`); return; }
    ctx.say(`“${current.title}” is in the library.`);
    go(`#/inbox/${nextAfter(current.id)}`);
    ctx.load();
  };
  const remove = async () => {
    if (!confirm(`Delete “${current.title}”? This can’t be undone.`)) return;
    setBusy(true);
    const { error } = await sb.from('recipes').delete().eq('id', current.id);
    setBusy(false);
    if (error) { ctx.say(`Couldn’t delete: ${error.message}`); return; }
    ctx.say('Deleted.');
    go(`#/inbox/${nextAfter(current.id)}`);
    ctx.load();
  };
  const approveAll = async () => {
    setBusy(true);
    const ids = ctx.inbox.map((r) => r.id);
    const { error } = await sb.from('recipes').update({ status: 'approved', approved_at: new Date().toISOString() }).in('id', ids);
    setBusy(false); setConfirmAll(false);
    if (error) { ctx.say(`Couldn’t approve: ${error.message}`); return; }
    ctx.say(`${ids.length} recipes added to the library.`);
    ctx.load();
  };
  const retry = async (job) => {
    const { error } = await sb.from('import_jobs').update({ status: 'pending', attempts: 0, error: null }).eq('id', job.id);
    if (error) ctx.say(`Couldn’t retry: ${error.message}`); else { ctx.say('Trying again — it takes about a minute.'); ctx.load(); }
  };
  const dismiss = async (job) => {
    const { error } = await sb.from('import_jobs').update({ status: 'cancelled' }).eq('id', job.id);
    if (error) ctx.say(`Couldn’t remove: ${error.message}`); else ctx.load();
  };

  const warnings = current ? (current.ai_warnings || []) : [];
  const done = warnings.filter((_, i) => checked[i]).length;
  return html`<main class="page">
    <div class="page-head"><h1 class="title">Inbox</h1>
      <span class="subtle">${ctx.inbox.length} to check${ctx.busyJobs.length ? ` · ${ctx.busyJobs.length} being read` : ''}</span></div>

    ${ctx.busyJobs.length > 0 && html`<div class="job"><span>Reading ${ctx.busyJobs.length} recipe${ctx.busyJobs.length === 1 ? '' : 's'}… they appear here when ready (about a minute each).</span></div>`}
    ${ctx.failedJobs.map((j) => html`<div class="job"><span><strong>Couldn’t read a recipe</strong> (${j.source === 'drive' ? 'Google Drive' : j.source === 'telegram' ? 'Telegram' : 'added here'}).<br /><span class="subtle">${j.error || 'Unknown problem.'}</span></span>
      <span class="row"><button class="btn" onClick=${() => retry(j)}>Retry</button><button class="btn danger" onClick=${() => dismiss(j)}>Remove</button></span></div>`)}

    ${!current ? html`<p class="empty">All caught up. New recipes appear here after you send screenshots to the bot, add them here, or run the Drive import.</p>` : html`
      <div class="row spread">
        <span class="subtle">Reviewing ${ctx.inbox.length - ctx.inbox.indexOf(current)} of ${ctx.inbox.length}</span>
        ${ctx.inbox.length > 1 && (confirmAll
          ? html`<span class="row"><span class="subtle">Approve all ${ctx.inbox.length} without checking?</span><button class="btn ok" onClick=${approveAll} disabled=${busy}>Yes, approve all</button><button class="btn" onClick=${() => setConfirmAll(false)}>Cancel</button></span>`
          : html`<button class="linkish" onClick=${() => setConfirmAll(true)}>Approve all…</button>`)}
      </div>
      ${warnings.length > 0 && html`<section><h2 style="font-size:16px;font-weight:600;margin-bottom:8px">Check these first · ${done} of ${warnings.length}</h2>
        <div class="checklist">${warnings.map((w, i) => html`<label><input type="checkbox" checked=${!!checked[i]} onChange=${() => setChecked({ ...checked, [i]: !checked[i] })} /><span>${w}</span></label>`)}</div></section>`}
      <${RecipeBody} r=${current} ctx=${ctx} />
      <div class="sticky-actions">
        <button class="btn ok big" onClick=${approve} disabled=${busy}>Approve</button>
        <a class="btn big" href=${`#/edit/${current.id}`}>Edit</a>
        <button class="btn danger big" onClick=${remove} disabled=${busy}>Delete</button>
        ${ctx.inbox.length > 1 && html`<button class="btn big" onClick=${() => go(`#/inbox/${nextAfter(current.id)}`)}>Skip</button>`}
      </div>`}
  </main>`;
}

// ---------------------------------------------------------------- edit
function EditPage({ ctx, id }) {
  const r = ctx.all.find((x) => x.id === id);
  const [f, setF] = useState(() => r && ({
    title: r.title || '', description: r.description || '', servings: r.servings ?? '', hands_on_min: r.hands_on_min ?? '', total_min: r.total_min ?? '',
    sections: [...(r.sections || [])], slow_cook: !!r.slow_cook, toddler_ok: r.toddler_ok === null ? '' : String(r.toddler_ok), toddler_notes: r.toddler_notes || '',
    lunchbox: r.lunchbox || '', lunchbox_notes: r.lunchbox_notes || '', tips: r.tips || '', heart_swaps: r.heart_swaps || '', source_url: r.source_url || '',
    ingredients: (r.ingredients || []).map((i) => i.display || i.item).join('\n'), steps: (r.steps || []).join('\n'),
  }));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (!r) return html`<main class="page"><p class="empty">Recipe not found. <a href="#/library">Back</a></p></main>`;
  const set = (k) => (e) => { setF({ ...f, [k]: e.currentTarget.type === 'checkbox' ? e.currentTarget.checked : e.currentTarget.value }); setError(''); };
  const num = (v) => (String(v).trim() === '' ? null : Math.max(0, Math.round(Number(v))));
  const save = async (e) => {
    e.preventDefault();
    if (!f.title.trim()) { setError('Give the recipe a title.'); return; }
    if ([f.servings, f.hands_on_min, f.total_min].some((v) => String(v).trim() !== '' && Number.isNaN(Number(v)))) { setError('Servings and times must be numbers.'); return; }
    if (f.source_url.trim() && !/^https?:\/\//i.test(f.source_url.trim())) { setError('The link must start with http:// or https://'); return; }
    const old = r.ingredients || [];
    const ingredients = f.ingredients.split('\n').map((s) => s.trim()).filter(Boolean).map((line) => {
      const same = old.find((i) => (i.display || i.item) === line);
      return same || { group: null, qty: null, unit: null, item: line.toLowerCase(), display: line, category: 'other', optional: false };
    });
    const patch = {
      title: f.title.trim(), description: f.description.trim() || null, servings: num(f.servings), hands_on_min: num(f.hands_on_min), total_min: num(f.total_min),
      sections: f.sections, slow_cook: f.slow_cook, toddler_ok: f.toddler_ok === '' ? null : f.toddler_ok === 'true', toddler_notes: f.toddler_notes.trim() || null,
      lunchbox: f.lunchbox || null, lunchbox_notes: f.lunchbox_notes.trim() || null, tips: f.tips.trim() || null, heart_swaps: f.heart_swaps.trim() || null,
      source_url: f.source_url.trim() || null, ingredients, steps: f.steps.split('\n').map((s) => s.trim()).filter(Boolean),
    };
    if (patch.servings !== null && (patch.servings < 1 || patch.servings > 60)) { setError('Servings must be between 1 and 60.'); return; }
    setBusy(true);
    const { error: err } = await sb.from('recipes').update(patch).eq('id', r.id);
    setBusy(false);
    if (err) { setError(`Couldn’t save: ${err.message}`); return; }
    ctx.say('Saved.');
    await ctx.load();
    go(r.status === 'inbox' ? `#/inbox/${r.id}` : `#/recipe/${r.id}`);
  };
  const del = async () => {
    if (!confirm(`Delete “${r.title}”? This can’t be undone.`)) return;
    const { error: err } = await sb.from('recipes').delete().eq('id', r.id);
    if (err) { setError(`Couldn’t delete: ${err.message}`); return; }
    ctx.say('Deleted.'); await ctx.load(); go(r.status === 'inbox' ? '#/inbox' : '#/library');
  };
  const toggleSection = (slug) => setF({ ...f, sections: f.sections.includes(slug) ? f.sections.filter((s) => s !== slug) : f.sections.concat(slug) });
  return html`<main class="page">
    <div class="row spread"><a class="btn" href=${r.status === 'inbox' ? `#/inbox/${r.id}` : `#/recipe/${r.id}`}>Cancel</a><h1 class="title" style="font-size:24px">Edit recipe</h1></div>
    <form onSubmit=${save} style="display:flex;flex-direction:column;gap:14px" novalidate>
      <label class="field">Title<input class="input" value=${f.title} onInput=${set('title')} /></label>
      <label class="field">Short description<textarea class="textarea" style="min-height:70px" value=${f.description} onInput=${set('description')}></textarea></label>
      <div class="stats">
        <label class="field">Serves<input class="input" inputmode="numeric" value=${f.servings} onInput=${set('servings')} /></label>
        <label class="field">Hands-on (min)<input class="input" inputmode="numeric" value=${f.hands_on_min} onInput=${set('hands_on_min')} /></label>
        <label class="field">Total (min)<input class="input" inputmode="numeric" value=${f.total_min} onInput=${set('total_min')} /></label>
        <label class="field" style="justify-content:flex-end"><span class="row" style="min-height:48px"><input type="checkbox" checked=${f.slow_cook} onChange=${set('slow_cook')} style="width:22px;height:22px" /> Slow cooker</span></label>
      </div>
      <fieldset style="border:none;padding:0;margin:0"><legend class="field" style="margin-bottom:6px">Sections</legend>
        <div class="chips wrap">${ctx.sections.map((s) => html`<button type="button" class="chip" aria-pressed=${f.sections.includes(s.slug) ? 'true' : 'false'} onClick=${() => toggleSection(s.slug)}>${s.label}</button>`)}</div></fieldset>
      <div class="boxes">
        <label class="field">Good for Tata?<select class="select" value=${f.toddler_ok} onChange=${set('toddler_ok')}><option value="">Not checked</option><option value="true">Yes, with tweaks</option><option value="false">Make a separate portion</option></select></label>
        <label class="field">Lunchbox<select class="select" value=${f.lunchbox} onChange=${set('lunchbox')}><option value="">Not checked</option><option value="ambient">Fine without an ice pack</option><option value="ice_pack">Needs an ice pack</option><option value="no">Not for the lunchbox</option></select></label>
      </div>
      <label class="field">Notes for Tata<textarea class="textarea" style="min-height:80px" value=${f.toddler_notes} onInput=${set('toddler_notes')}></textarea></label>
      <label class="field">Lunchbox notes<textarea class="textarea" style="min-height:70px" value=${f.lunchbox_notes} onInput=${set('lunchbox_notes')}></textarea></label>
      <label class="field">Ingredients (one per line)<textarea class="textarea" style="min-height:200px" value=${f.ingredients} onInput=${set('ingredients')}></textarea></label>
      <label class="field">Method (one step per line)<textarea class="textarea" style="min-height:200px" value=${f.steps} onInput=${set('steps')}></textarea></label>
      <label class="field">Tips<textarea class="textarea" style="min-height:70px" value=${f.tips} onInput=${set('tips')}></textarea></label>
      <label class="field">Make it lighter (swaps)<textarea class="textarea" style="min-height:70px" value=${f.heart_swaps} onInput=${set('heart_swaps')}></textarea></label>
      <label class="field">Link to the original post<input class="input" type="url" value=${f.source_url} onInput=${set('source_url')} /></label>
      ${error && html`<p class="error" role="alert">${error}</p>`}
      <div class="row"><button class="btn primary big" type="submit" disabled=${busy}>${busy ? 'Saving…' : 'Save'}</button>
        <button class="btn danger big" type="button" onClick=${del}>Delete recipe</button></div>
    </form>
  </main>`;
}

// ---------------------------------------------------------------- add
async function toJpeg(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error('Couldn’t open that picture.')); i.src = url; });
    const max = 2576;
    const s = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
    const c = document.createElement('canvas');
    c.width = Math.round(img.naturalWidth * s); c.height = Math.round(img.naturalHeight * s);
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    return await new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error('Couldn’t convert that picture.'))), 'image/jpeg', 0.86));
  } finally { URL.revokeObjectURL(url); }
}

function AddPage({ ctx }) {
  const [files, setFiles] = useState([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [doneMsg, setDoneMsg] = useState('');
  const input = useRef(null);
  useEffect(() => () => files.forEach((f) => URL.revokeObjectURL(f.preview)), [files]);
  const pick = (e) => {
    const chosen = [...(e.currentTarget.files || [])].filter((f) => f.type.startsWith('image/') || /\.(heic|heif)$/i.test(f.name));
    setFiles(files.concat(chosen.map((file) => ({ file, preview: URL.createObjectURL(file) }))).slice(0, 20));
    setError(''); setDoneMsg('');
    e.currentTarget.value = '';
  };
  const removeAt = (i) => setFiles(files.filter((_, j) => j !== i));
  const send = async () => {
    if (!files.length && !text.trim()) { setError('Choose at least one screenshot, or paste the recipe text.'); return; }
    setError('');
    const jobId = crypto.randomUUID();
    const paths = [];
    try {
      for (let i = 0; i < files.length; i++) {
        setBusy(`Uploading ${i + 1} of ${files.length}…`);
        const blob = await toJpeg(files[i].file);
        const path = `app/${jobId}/${String(i + 1).padStart(2, '0')}.jpg`;
        const { error: err } = await sb.storage.from(BUCKET).upload(path, blob, { contentType: 'image/jpeg', upsert: true });
        if (err) throw new Error(`Upload failed: ${err.message}`);
        paths.push(path);
      }
      setBusy('Sending for reading…');
      const { error: err } = await sb.from('import_jobs').insert({ id: jobId, source: 'app', status: 'pending', images: paths, texts: text.trim() ? [text.trim()] : [] });
      if (err) throw new Error(`Couldn’t send it: ${err.message}`);
      setFiles([]); setText('');
      setDoneMsg('Sent! The recipe will be in the Inbox in about a minute.');
      ctx.load();
    } catch (e) { setError(e.message || String(e)); } finally { setBusy(''); }
  };
  return html`<main class="page">
    <h1 class="title">Add a recipe</h1>
    <p class="subtle" style="margin:0">One recipe at a time. Include the screenshot that shows the finished dish — it becomes the picture.</p>
    <input ref=${input} type="file" accept="image/*" multiple class="sr-only" id="pick" onChange=${pick} />
    <label class="drop" for="pick"><strong style="font-size:17px">Choose screenshots</strong><span class="subtle">From Photos — in the order you took them</span></label>
    ${files.length > 0 && html`<div class="thumbs">${files.map((f, i) => html`<div class="thumb"><img src=${f.preview} alt=${`Screenshot ${i + 1}`} /><span class="n">${i + 1}</span>
      <button type="button" aria-label=${`Remove screenshot ${i + 1}`} onClick=${() => removeAt(i)}>✕</button></div>`)}</div>`}
    <label class="field">Link or notes (optional)<textarea class="textarea" style="min-height:90px" placeholder="https://www.instagram.com/reel/…" value=${text} onInput=${(e) => { setText(e.currentTarget.value); setError(''); }}></textarea></label>
    ${error && html`<p class="error" role="alert">${error}</p>`}
    ${doneMsg && html`<p class="notice" role="status">${doneMsg} <a href="#/inbox">Go to Inbox</a></p>`}
    <button class="btn primary big" onClick=${send} disabled=${!!busy}>${busy || 'Add recipe'}</button>
    <p class="subtle" style="margin:0">On your phone you can also send screenshots to the Telegram bot.</p>
  </main>`;
}

// ---------------------------------------------------------------- week (phase 3)
function WeekPage({ ctx }) {
  return html`<main class="page">
    <h1 class="title">This week</h1>
    <p class="empty">Coming next: dinners and Sisi’s lunchbox for Monday to Friday, three suggestions per slot, batch-cook nights that book a freezer dinner later in the week, and one shopping list split into Unicorn, the farm shop and Sainsbury’s.</p>
    <div class="row spread"><span class="subtle">Logged in as ${ctx.me} · app ${APP_VERSION}</span>
      <button class="btn" onClick=${() => sb.auth.signOut()}>Log out</button></div>
  </main>`;
}

render(html`<${App} />`, document.getElementById('app'));
