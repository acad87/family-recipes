#!/usr/bin/env python3
"""Builds the n8n workflow JSON files for Family Recipes.

Run:  python3 n8n/build.py            -> writes n8n/*.json (production settings)
      python3 n8n/build.py --test URL -> writes into n8n/test/ pointing every
                                         external service at a local mock URL
"""
import json, sys, uuid, re, pathlib

OUT = pathlib.Path(__file__).parent
TEST = len(sys.argv) > 2 and sys.argv[1] == "--test"
MOCK = sys.argv[2].rstrip("/") if TEST else None

# ---- fixed ids so the workflows can reference each other after CLI import ----
WF_EXTRACT = "RcpExtract0000001"
WF_TELEGRAM = "RcpTelegram000002"
WF_PROCESS = "RcpProcess0000003"
WF_DRIVE = "RcpDriveAll000004"
WF_DRIVE_ONE = "RcpDriveOne000005"

CRED = {
    "supabase": {"httpHeaderAuth": {"id": "rcpSupabaseKey001", "name": "Supabase secret key"}},
    "anthropic": {"httpHeaderAuth": {"id": "rcpAnthropicKey01", "name": "Anthropic API key"}},
    "jobsecret": {"httpHeaderAuth": {"id": "rcpJobSecret00001", "name": "Recipes job secret"}},
    "telegram": {"telegramApi": {"id": "rcpTelegramBot001", "name": "Recipes Telegram bot"}},
    "drive": {"googleDriveOAuth2Api": {"id": "rcpGoogleDrive001", "name": "Google Drive"}},
}

import os
SUPABASE_URL = MOCK if TEST else os.environ.get("RCP_SUPABASE_URL", "https://YOUR-PROJECT.supabase.co")
ANTHROPIC_URL = (MOCK + "/anthropic/v1/messages") if TEST else "https://api.anthropic.com/v1/messages"
DRIVE_API = (MOCK + "/drive") if TEST else "https://www.googleapis.com"
TELEGRAM_IDS = "111,222" if TEST else os.environ.get("RCP_TELEGRAM_IDS", "PUT-YOUR-TELEGRAM-ID,PUT-LAURAS-TELEGRAM-ID")


_counter = [0]


def nid():
    """Deterministic ids so rebuilding produces identical files."""
    _counter[0] += 1
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rcp-{_counter[0]}"))


def check_expr(s):
    # n8n ends an expression at the first "}}" — make sure our inline JS never contains one early.
    if isinstance(s, str) and s.startswith("="):
        for m in re.finditer(r"\{\{(.*?)\}\}", s, flags=re.S):
            assert "{{" not in m.group(1), f"nested braces in expression: {s[:80]}"
    return s


class WF:
    def __init__(self, name, wid):
        self.name, self.id = name, wid
        self.nodes, self.conns = [], {}
        self.x = 0

    def add(self, name, ntype, ver, params, pos, creds=None, **extra):
        def walk(v):
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            if isinstance(v, list):
                return [walk(x) for x in v]
            return check_expr(v)
        node = {"parameters": walk(params), "id": nid(), "name": name, "type": ntype,
                "typeVersion": ver, "position": list(pos)}
        if creds:
            node["credentials"] = creds
        node.update(extra)
        self.nodes.append(node)
        return name

    def link(self, src, dst, out=0, inp=0):
        main = self.conns.setdefault(src, {"main": []})["main"]
        while len(main) <= out:
            main.append([])
        main[out].append({"node": dst, "type": "main", "index": inp})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.link(a, b)

    def dump(self, filename):
        data = {"name": self.name, "id": self.id, "nodes": self.nodes, "connections": self.conns,
                "settings": {"executionOrder": "v1", "saveManualExecutions": True,
                             "callerPolicy": "workflowsFromSameOwner"},
                "pinData": {}, "active": False, "meta": {"templateCredsSetupCompleted": False},
                "tags": []}
        d = OUT / "test" if TEST else pathlib.Path(os.environ.get("RCP_OUT", OUT))
        d.mkdir(exist_ok=True)
        (d / filename).write_text(json.dumps(data, indent=2, ensure_ascii=False))
        # every connection target must exist
        names = {n["name"] for n in self.nodes}
        for s, c in self.conns.items():
            assert s in names, s
            for outs in c["main"]:
                for t in outs:
                    assert t["node"] in names, t


# ---------------------------------------------------------------- node helpers
def settings(wf, pos, values):
    return wf.add("Settings", "n8n-nodes-base.set", 3.4, {
        "assignments": {"assignments": [
            {"id": nid(), "name": k, "value": v, "type": "number" if isinstance(v, (int, float)) else "string"}
            for k, v in values.items()]},
        "options": {}}, pos)


def http(wf, name, pos, method, url, cred=None, query=None, headers=None, body=None,
         binary_field=None, file_response=False, timeout=None, json_response=False, **extra):
    p = {"method": method, "url": url}
    if cred == "drive":
        p.update({"authentication": "predefinedCredentialType", "nodeCredentialType": "googleDriveOAuth2Api"})
    elif cred:
        p.update({"authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth"})
    if query:
        p.update({"sendQuery": True, "queryParameters": {"parameters": [{"name": k, "value": v} for k, v in query.items()]}})
    if headers:
        p.update({"sendHeaders": True, "headerParameters": {"parameters": [{"name": k, "value": v} for k, v in headers.items()]}})
    if body is not None:
        p.update({"sendBody": True, "contentType": "json", "specifyBody": "json", "jsonBody": body})
    if binary_field:
        p.update({"sendBody": True, "contentType": "binaryData", "inputDataFieldName": binary_field})
    opts = {}
    if file_response:
        opts["response"] = {"response": {"responseFormat": "file"}}
    elif json_response or (headers and "Accept" in headers):
        # PostgREST single-object replies use a vendor content type n8n would treat as text
        opts["response"] = {"response": {"responseFormat": "json"}}
    if timeout:
        opts["timeout"] = timeout
    p["options"] = opts
    return wf.add(name, "n8n-nodes-base.httpRequest", 4.2, p, pos, CRED[cred] if cred else None, **extra)


def code(wf, name, pos, js, each=False, **extra):
    p = {"jsCode": js.strip()}
    if each:
        p["mode"] = "runOnceForEachItem"
    return wf.add(name, "n8n-nodes-base.code", 2, p, pos, **extra)


def if_true(wf, name, pos, expr):
    return wf.add(name, "n8n-nodes-base.if", 2.2, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
            "conditions": [{"id": nid(), "leftValue": expr, "rightValue": "",
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
            "combinator": "and"},
        "looseTypeValidation": True, "options": {}}, pos)


def switch_n(wf, name, pos, n, expr):
    return wf.add(name, "n8n-nodes-base.switch", 3.2,
                  {"mode": "expression", "numberOutputs": n, "output": expr}, pos)


def tg_send(wf, name, pos, chat_expr, text_expr, buttons=None, **extra):
    p = {"resource": "message", "operation": "sendMessage", "chatId": chat_expr, "text": text_expr,
         "additionalFields": {"appendAttribution": False, "parse_mode": "HTML"}}
    if buttons:
        p["replyMarkup"] = "inlineKeyboard"
        p["inlineKeyboard"] = {"rows": [{"row": {"buttons": [
            {"text": t, "additionalFields": {"callback_data": cb}} for t, cb in row]}} for row in buttons]}
    return wf.add(name, "n8n-nodes-base.telegram", 1.2, p, pos, CRED["telegram"], **extra)


def exec_wf(wf, name, pos, wid, wname, inputs, schema_types, **extra):
    return wf.add(name, "n8n-nodes-base.executeWorkflow", 1.2, {
        "workflowId": {"__rl": True, "value": wid, "mode": "list", "cachedResultName": wname},
        "workflowInputs": {
            "mappingMode": "defineBelow", "value": inputs, "matchingColumns": [],
            "schema": [{"id": k, "displayName": k, "required": False, "defaultMatch": False, "display": True,
                        "canBeUsedToMatch": True, "type": t, "removed": False} for k, t in schema_types.items()],
            "attemptToConvertTypes": False, "convertFieldsToString": False},
        "options": {"waitForSubWorkflow": True}}, pos, **extra)


SB = "{{ $('Settings').first().json.supabase_url }}"


def esc(js):
    """HTML-escape a value inside a Telegram message expression."""
    return "{{ String(" + js + " ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') }}"
REST = "=" + SB + "/rest/v1"
OBJ = {"Accept": "application/vnd.pgrst.object+json"}
ERR = {"onError": "continueErrorOutput"}

# =====================================================================
# 1. Extract recipe (sub-workflow): job_id -> Claude -> inbox recipe
# =====================================================================
EXTRACT_NAME = "Recipes · 1 Extract recipe"
w = WF(EXTRACT_NAME, WF_EXTRACT)
w.add("When called", "n8n-nodes-base.executeWorkflowTrigger", 1.1,
      {"inputSource": "workflowInputs", "workflowInputs": {"values": [{"name": "job_id", "type": "string"}]}}, (0, 300))
settings(w, (220, 300), {"supabase_url": SUPABASE_URL, "anthropic_url": ANTHROPIC_URL,
                         "model": "claude-sonnet-5-5", "max_tokens": 16000})
http(w, "Get job", (440, 300), "GET", REST + "/import_jobs", "supabase",
     query={"id": "=eq.{{ $('When called').first().json.job_id }}",
            "select": "id,source,status,images,texts,tg_chat_id"},
     headers=OBJ, **ERR)
http(w, "Get tags", (660, 200), "GET", REST + "/tags", "supabase",
     query={"select": "kind,slug,label", "order": "kind,sort"}, executeOnce=True, **ERR)
http(w, "Get shop categories", (880, 200), "GET", REST + "/shop_categories", "supabase",
     query={"select": "category,label", "order": "sort"}, executeOnce=True, **ERR)
code(w, "Prepare", (1100, 200), **ERR, js=r"""
const job = $('Get job').first().json;
const tags = $('Get tags').all().map(i => i.json);
const cats = $('Get shop categories').all().map(i => i.json);
const byKind = k => tags.filter(t => t.kind === k);
return [{ json: {
  job_id: job.id, tg_chat_id: job.tg_chat_id ?? null,
  // Claude rejects full-size screenshots when a request has more than 20 images
  images: (job.images || []).slice(0, 20), texts: job.texts || [],
  too_many: (job.images || []).length > 20,
  needs_signing: (job.images || []).length > 0,
  sections: byKind('section'), nutrients: byKind('nutrient'), allergens: byKind('allergen'),
  equipment: byKind('equipment'), reheat: byKind('reheat'), categories: cats,
} }];
""")
if_true(w, "Has screenshots?", (1320, 200), "={{ $json.needs_signing }}")
http(w, "Sign screenshot links", (1540, 100), "POST",
     "=" + SB + "/storage/v1/object/sign/recipe-images", "supabase",
     body="={{ JSON.stringify({ expiresIn: 1800, paths: $json.images }) }}", **ERR)

BUILD_JS = r"""
const p = $('Prepare').first().json;
const s = $('Settings').first().json;
const base = s.supabase_url.replace(/\/$/, '') + '/storage/v1';

// Signed links, kept in the original screenshot order
let links = [];
if (p.needs_signing) {
  const signed = $('Sign screenshot links').all().map(i => i.json);
  links = p.images.map(path => {
    const hit = signed.find(x => x.path === path);
    const u = hit && (hit.signedURL || hit.signedUrl);
    if (!u) throw new Error('Could not create a link for screenshot ' + path);
    return u.startsWith('http') ? u : base + u;
  });
}

const slugs = list => list.map(t => t.slug);
const legend = list => list.map(t => `${t.slug} = ${t.label}`).join('; ');
const nullable = t => ({ type: [t, 'null'] });
// strict schemas reject enum + type array; a choice-or-empty field is written as anyOf
const oneOrNull = values => ({ anyOf: [{ type: 'string', enum: values }, { type: 'null' }] });

const schema = {
  type: 'object',
  properties: {
    is_recipe: { type: 'boolean', description: 'false if the material does not contain a recipe' },
    title: { type: 'string', description: 'Short, clear recipe name in English' },
    description: { type: 'string', description: 'One short sentence that helps a parent decide' },
    source_name: { type: 'string', description: 'Author or Instagram handle if visible (e.g. @handle), else empty' },
    sections: { type: 'array', items: { type: 'string', enum: slugs(p.sections) } },
    servings: { ...nullable('integer'), description: 'Number of portions the recipe as written makes' },
    slow_cook: { type: 'boolean', description: 'true if cooked low and slow for 2+ hours (slow cooker, oven braise)' },
    hands_on_min: { ...nullable('integer'), description: 'Active prep/cooking minutes' },
    total_min: { ...nullable('integer'), description: 'Total minutes start to finish, including oven/simmer/rest time' },
    effort: oneOrNull(['weeknight', 'weekend']),
    cost: oneOrNull(['£', '££', '£££']),
    fridge_days: { ...nullable('integer'), description: 'Days the cooked dish keeps in the fridge' },
    freezer_months: { ...nullable('integer'), description: 'Months it keeps frozen; 0 = does not freeze well' },
    reheat: { type: 'array', items: { type: 'string', enum: slugs(p.reheat) } },
    equipment: { type: 'array', items: { type: 'string', enum: slugs(p.equipment) } },
    lunchbox: oneOrNull(['ambient', 'ice_pack', 'no']),
    lunchbox_notes: { type: 'string' },
    toddler_ok: { type: ['boolean', 'null'] },
    toddler_notes: { type: 'string' },
    hidden_veg: { type: 'integer', description: 'How many vegetables are blended/grated in so kids do not notice' },
    nutrients: { type: 'array', items: { type: 'string', enum: slugs(p.nutrients) } },
    allergens: { type: 'array', items: { type: 'string', enum: slugs(p.allergens) } },
    ingredients: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          group: { type: ['string', 'null'], description: 'Sub-heading such as "Sauce" or "Topping", else null' },
          qty: { type: ['number', 'null'] },
          unit: { type: ['string', 'null'] },
          item: { type: 'string' },
          display: { type: 'string' },
          category: { type: 'string', enum: p.categories.map(c => c.category) },
          optional: { type: 'boolean' },
        },
        required: ['qty', 'unit', 'item', 'display', 'category'],
      },
    },
    steps: { type: 'array', items: { type: 'string' } },
    tips: { type: 'string', description: 'Useful tips, swaps or serving ideas from the source; empty if none' },
    warnings: { type: 'array', items: { type: 'string' }, description: 'Things the parent should double-check' },
    cover: {
      description: 'Where the photo of the finished dish is, or null if no screenshot shows it',
      anyOf: [
        {
          type: 'object',
          properties: {
            screenshot: { type: 'integer', description: 'Screenshot number as labelled (1 = first)' },
            left: { type: 'number', description: 'Left edge of the dish photo, 0 = left side of the screenshot, 1 = right side' },
            top: { type: 'number', description: 'Top edge, 0 = top of the screenshot, 1 = bottom' },
            right: { type: 'number' },
            bottom: { type: 'number' },
          },
          required: ['screenshot', 'left', 'top', 'right', 'bottom'],
        },
        { type: 'null' },
      ],
    },
  },
  required: ['is_recipe', 'title', 'sections', 'servings', 'total_min', 'ingredients', 'steps',
             'allergens', 'nutrients', 'lunchbox', 'fridge_days', 'freezer_months', 'warnings'],
};

// Claude Sonnet 5.5 rejects forced tool_choice, so we use tool_choice "auto" plus a
// strict tool. Strict tools need additionalProperties:false and (to stay within the
// 24-optional-parameter limit) every property listed as required. Set STRICT = false
// only if Anthropic ever answers "Schema is too complex for compilation".
const STRICT = true;
function strictify(node) {
  if (node && typeof node === 'object') {
    if (node.type === 'object' && node.properties) {
      node.additionalProperties = false;
      node.required = Object.keys(node.properties);
      Object.values(node.properties).forEach(strictify);
    }
    if (node.items) strictify(node.items);
    if (Array.isArray(node.anyOf)) node.anyOf.forEach(strictify);
  }
  return node;
}
if (STRICT) strictify(schema);

const system = `You turn recipe screenshots (usually Instagram posts) into structured recipes for a family in Manchester, UK: two adults and two young children (about 4 and 1). They cook from scratch. Always answer by calling the save_recipe tool exactly once — never reply with plain text, even when there is no recipe (then set is_recipe to false). Use an empty string for text fields you have nothing for.

TRANSCRIBE FAITHFULLY
- Only use ingredients, quantities and steps that appear in the material. Never invent them.
- If text is cut off, blurry or missing (for example the method is on a screenshot that was not sent), say so in warnings.
- Screenshots are in order; a recipe may continue across several. Ignore likes, comments and app buttons.
- If there is more than one recipe, extract the main one and add a warning.
- If there is no recipe at all, set is_recipe to false and fill the other fields minimally.

UK CONVENTIONS
- British English names (courgette, aubergine, coriander, spring onion, double cream, plain flour, bicarbonate of soda).
- Metric. Convert cups/oz/°F in "display" and keep the original in brackets, e.g. "200 g plain flour (1½ cups)". Oven: "200°C (180°C fan)".

INGREDIENTS
- item: the generic shopping name, lowercase, singular, without preparation words, so the same thing merges across recipes ("red onion", "chicken thigh", "chopped tomatoes", "plain flour", "egg", "garlic").
- qty + unit describe how much to buy for the recipe as written; unit is one of g, kg, ml, l, tsp, tbsp, pinch, clove, tin, pack, bunch, slice, or null for whole items (2 eggs → qty 2, unit null). Use null qty for "to taste".
- display: the full line as a cook would read it ("2 red onions, finely sliced").
- category for the shop it comes from: ${legend(p.categories.map(c => ({ slug: c.category, label: c.label })))}.

STEPS
- Short imperative steps in order. Split long paragraphs into separate steps. Keep timings and temperatures.

TAGS (estimates are fine, but be conservative and add a warning when a value is a pure guess)
- sections (choose all that fit): ${legend(p.sections)}.
  packed_lunch only if it is good cold or at room temperature and easy for a 4-year-old to eat from a lunchbox. batch_freeze if it makes 6+ portions and freezes well. quick if total time is 25 minutes or less. weekend if it is long or fiddly.
- effort: weeknight if hands-on time is about 30 minutes or less and nothing is fiddly; otherwise weekend.
- cost per portion with UK supermarket prices: £ under about £1.50, ££ about £1.50–£3, £££ above.
- fridge_days, following UK Food Standards Agency advice on leftovers: cooked rice dishes 1; other cooked dishes (meat, fish, egg, veg, pasta) 2; plain bakes without cream or custard 3–5. freezer_months: usually 3 for cooked dishes; 0 for things that freeze badly (salads, fried crispy food, mayo or cream-based sauces, boiled eggs). Where relevant, put in tips: cool and chill within 1–2 hours, reheat only once until steaming hot, eat defrosted food within 24 hours.
- reheat: ${legend(p.reheat)}. equipment: ${legend(p.equipment)}.
- lunchbox: "ambient" only for foods safe unrefrigerated for about 4 hours (plain bread or crackers, whole or cut fruit, raw veg sticks, plain bakes without cream, custard, cheese or meat). Anything containing cheese, hummus, yoghurt, cooked meat, fish, egg, cooked rice or pasta, or creamy sauces → "ice_pack". "no" if it must be hot, is messy, or is unsuitable for a school lunchbox. For rice dishes add to lunchbox_notes: cool quickly, keep chilled, eat within 24 hours. Many UK schools are nut-free: if it contains nuts, say so in lunchbox_notes.
- toddler_ok: true if a portion suits a 1-year-old or can be taken out before adding salt, chilli, whole nuts or other unsuitable items. In toddler_notes give the adaptation and choking advice where relevant (cut grapes and cherry tomatoes lengthways, no whole nuts for under-5s, no added salt for babies).
- nutrients (only when clearly a meaningful source per portion): ${legend(p.nutrients)}.
- allergens, UK's 14 (always add the warning "Allergens are estimated — check product labels" if any packaged ingredient is used; judge from the ingredients; e.g. pasta/bread/flour → gluten, butter/cheese/milk → milk, pesto → tree_nuts and milk, soy sauce → soya and gluten, Worcestershire sauce → fish; do not guess beyond likely ingredients): ${legend(p.allergens)}.
- hidden_veg: count only vegetables that are blended, grated or otherwise hidden.
- warnings: anything a parent should check before trusting the recipe (missing quantities, estimated times, unreadable parts, food safety concerns).

PICTURE
- cover: if one of the screenshots shows a photo or video frame of the finished dish, give that screenshot's number and the box around just the food photo, as fractions of the screenshot (left/top/right/bottom between 0 and 1). Keep the box inside the photo itself: exclude the status bar, profile row, buttons, likes and caption text. Prefer the most appetising, least cluttered shot. If no screenshot shows the dish (only text or ingredients), use null.`;

const content = [];
links.forEach((url, i) => {
  content.push({ type: 'text', text: `Screenshot ${i + 1} of ${links.length}:` });
  content.push({ type: 'image', source: { type: 'url', url } });
});
if (p.texts.length) {
  content.push({ type: 'text', text: 'Text sent with the recipe (captions or pasted text):\n\n' + p.texts.join('\n\n---\n\n') });
}
if (p.too_many) content.push({ type: 'text', text: 'Note: only the first 20 screenshots were sent. Add a warning that the rest were not read.' });
content.push({ type: 'text', text: 'Extract this recipe and call save_recipe.' });

return [{ json: { body: {
  model: s.model,
  max_tokens: s.max_tokens,
  system,
  tools: [{ name: 'save_recipe', description: 'Save the extracted recipe. Always call this exactly once.', strict: STRICT, input_schema: schema }],
  tool_choice: { type: 'auto' },
  messages: [{ role: 'user', content }],
} } }];
"""
code(w, "Build Claude request", (1760, 200), BUILD_JS, **ERR)
http(w, "Ask Claude", (1980, 200), "POST", "={{ $('Settings').first().json.anthropic_url }}", "anthropic",
     headers={"anthropic-version": "2023-06-01"},
     body="={{ JSON.stringify($json.body) }}", timeout=240000,
     retryOnFail=True, maxTries=2, waitBetweenTries=20000, **ERR)
code(w, "Read answer", (2200, 200), r"""
const r = $json;
if (r.stop_reason === 'max_tokens') throw new Error('The recipe was too long to read in one go.');
const call = (r.content || []).find(c => c.type === 'tool_use' && c.name === 'save_recipe');
if (!call) throw new Error('Claude did not return a recipe.');
const recipe = call.input || {};
if (recipe.is_recipe === false) throw new Error("These screenshots don't look like a recipe.");
// Occasionally arrays come back as JSON text; turn them back into arrays
for (const k of ['sections', 'reheat', 'equipment', 'nutrients', 'allergens', 'ingredients', 'steps', 'warnings']) {
  if (typeof recipe[k] === 'string') { try { recipe[k] = JSON.parse(recipe[k]); } catch (e) { recipe[k] = []; } }
  if (!Array.isArray(recipe[k])) recipe[k] = [];
}
if (!recipe.ingredients.length) throw new Error('No ingredients could be read. The ingredient list may be on a screenshot that was not sent.');
if (!recipe.steps.length) recipe.warnings.push('No method steps could be read — check the original post.');
return [{ json: { recipe, model: r.model || $('Settings').first().json.model } }];
""", **ERR)
http(w, "Save to inbox", (2420, 200), "POST", REST + "/rpc/complete_job", "supabase",
     body="={{ JSON.stringify({ p_job_id: $('Prepare').first().json.job_id, p_recipe: $json.recipe, p_model: $json.model }) }}",
     **ERR)
w.add("Result", "n8n-nodes-base.set", 3.4, {"assignments": {"assignments": [
    {"id": nid(), "name": "ok", "value": "={{ true }}", "type": "boolean"},
    {"id": nid(), "name": "job_id", "value": "={{ $('When called').first().json.job_id }}", "type": "string"},
    {"id": nid(), "name": "recipe_id", "value": "={{ $json.recipe_id }}", "type": "string"},
    {"id": nid(), "name": "title", "value": "={{ $json.title }}", "type": "string"},
    {"id": nid(), "name": "tg_chat_id", "value": "={{ $('Get job').first().json.tg_chat_id }}", "type": "string"},
    {"id": nid(), "name": "error", "value": "", "type": "string"}]}, "options": {}}, (2640, 200))
code(w, "Error message", (1980, 520), r"""
const e = $json.error ?? $json;
let msg = typeof e === 'string' ? e : (e.message || e.description || JSON.stringify(e));
const d = typeof e === 'object' && e ? (e.description || '') : '';
if (d && !msg.includes(d)) msg += ' — ' + d;
msg = String(msg).replace(/\s*\[line \d+(, for item \d+)?\]\s*$/, '');
// Friendlier wording for the usual API problems
if (/overloaded|529|rate_limit|429/i.test(msg)) msg = 'Claude is busy right now. Tap Retry in a few minutes.';
else if (/credit balance/i.test(msg)) msg = 'The Anthropic account is out of credit. Top it up, then tap Retry.';
else if (/authentication_error|invalid x-api-key|401/i.test(msg)) msg = 'n8n could not log in to a service (check the API keys in n8n), then tap Retry.';
else if (/ECONNREFUSED|ETIMEDOUT|ENOTFOUND|timeout/i.test(msg)) msg = 'A connection timed out. Tap Retry.';
let tg_chat_id = null;
// first(0): this node is wired to Get job's *error* output, so name the main output explicitly
try { tg_chat_id = $('Get job').first(0)?.json?.tg_chat_id ?? null; } catch (err) { /* job could not be loaded */ }
return [{ json: { error: msg.slice(0, 500), tg_chat_id } }];
""")
http(w, "Mark job failed", (2200, 520), "POST", REST + "/rpc/fail_job", "supabase",
     body="={{ JSON.stringify({ p_job_id: $('When called').first().json.job_id, p_error: $json.error }) }}",
     onError="continueRegularOutput")
w.add("Failed", "n8n-nodes-base.set", 3.4, {"assignments": {"assignments": [
    {"id": nid(), "name": "ok", "value": "={{ false }}", "type": "boolean"},
    {"id": nid(), "name": "job_id", "value": "={{ $('When called').first().json.job_id }}", "type": "string"},
    {"id": nid(), "name": "recipe_id", "value": "", "type": "string"},
    {"id": nid(), "name": "title", "value": "", "type": "string"},
    {"id": nid(), "name": "tg_chat_id", "value": "={{ $('Error message').first().json.tg_chat_id ?? '' }}", "type": "string"},
    {"id": nid(), "name": "error", "value": "={{ $('Error message').first().json.error }}", "type": "string"}]}, "options": {}}, (2420, 520))

w.chain("When called", "Settings", "Get job")
w.link("Get job", "Get tags")
w.link("Get tags", "Get shop categories")
w.link("Get shop categories", "Prepare")
w.link("Prepare", "Has screenshots?")
w.link("Has screenshots?", "Sign screenshot links", 0)
w.link("Has screenshots?", "Build Claude request", 1)
w.link("Sign screenshot links", "Build Claude request")
w.chain("Build Claude request", "Ask Claude", "Read answer", "Save to inbox", "Result")
for n in ["Get job", "Get tags", "Get shop categories", "Prepare", "Sign screenshot links", "Build Claude request",
          "Ask Claude", "Read answer", "Save to inbox"]:
    w.link(n, "Error message", 1)
w.chain("Error message", "Mark job failed", "Failed")
w.dump("1-extract-recipe.json")

# =====================================================================
# 2. Telegram bot: collect screenshots, ask "Done?", hand over
# =====================================================================
w = WF("Recipes · 2 Telegram bot", WF_TELEGRAM)
w.add("Telegram", "n8n-nodes-base.telegramTrigger", 1.2,
      {"updates": ["message", "callback_query"], "additionalFields": {}}, (0, 400), CRED["telegram"],
      webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, "rcp-telegram-trigger")))
settings(w, (220, 400), {"supabase_url": SUPABASE_URL, "allowed_telegram_ids": TELEGRAM_IDS, "wait_seconds": 15})
code(w, "Understand message", (440, 400), r"""
const u = $('Telegram').first().json;
const s = $('Settings').first().json;
const allowed = String(s.allowed_telegram_ids).split(',').map(x => x.trim()).filter(Boolean);
const from = u.callback_query?.from?.id ?? u.message?.from?.id;
const chat = u.callback_query?.message?.chat?.id ?? u.message?.chat?.id;
const out = { route: 4, chat_id: chat, user_id: from };
// route: 0 button tap, 1 screenshot/text for the recipe, 2 help, 3 unsupported file, 4 ignore
if (!from || !allowed.includes(String(from))) return [{ json: out }];

if (u.callback_query) {
  const [action, job_id] = String(u.callback_query.data || '').split(':');
  return [{ json: { ...out, route: 0, cb_id: u.callback_query.id, action, job_id,
                    button: action === 'x' ? 1 : 0 } }];
}
const m = u.message;
if (!m) return [{ json: out }];
const okTypes = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp' };
let file_id = null, mime = null;
if (Array.isArray(m.photo) && m.photo.length) {
  const best = m.photo.reduce((a, b) => ((b.width || 0) * (b.height || 0) > (a.width || 0) * (a.height || 0) ? b : a));
  file_id = best.file_id;                          // largest size Telegram has
  mime = 'image/jpeg';
} else if (m.document) {
  if (!okTypes[m.document.mime_type]) return [{ json: { ...out, route: 3 } }];
  file_id = m.document.file_id; mime = m.document.mime_type;
}
const text = (m.caption || m.text || '').trim();
if (!file_id && /^\/(start|help)/i.test(text)) return [{ json: { ...out, route: 2 } }];
if (!file_id && !text) return [{ json: { ...out, route: 3 } }];
const path = file_id ? `telegram/${chat}/${m.message_id}-${u.update_id}.${okTypes[mime]}` : null;
return [{ json: { ...out, route: 1, file_id, mime, path, text: text || null, has_file: !!file_id } }];
""")
switch_n(w, "Route", (660, 400), 5, "={{ $json.route }}")

# --- screenshot / text part
if_true(w, "Is screenshot?", (880, 300), "={{ $json.has_file }}")
w.add("Download screenshot", "n8n-nodes-base.telegram", 1.2,
      {"resource": "file", "operation": "get", "fileId": "={{ $json.file_id }}", "download": True,
       "additionalFields": {}}, (1100, 200), CRED["telegram"], **ERR)
http(w, "Upload screenshot", (1320, 200), "POST",
     "=" + SB + "/storage/v1/object/recipe-images/{{ $('Understand message').first().json.path }}", "supabase",
     headers={"Content-Type": "={{ $('Understand message').first().json.mime }}", "x-upsert": "true"},
     binary_field="data", **ERR)
http(w, "Add screenshot to recipe", (1540, 200), "POST", REST + "/rpc/tg_add_part", "supabase",
     body="={{ JSON.stringify({ p_chat_id: $('Understand message').first().json.chat_id, p_user_id: $('Understand message').first().json.user_id, p_image: $('Understand message').first().json.path, p_text: $('Understand message').first().json.text }) }}")
http(w, "Add text to recipe", (1320, 400), "POST", REST + "/rpc/tg_add_part", "supabase",
     body="={{ JSON.stringify({ p_chat_id: $json.chat_id, p_user_id: $json.user_id, p_image: null, p_text: $json.text }) }}")
w.add("Wait for more", "n8n-nodes-base.wait", 1.1,
      {"amount": "={{ $('Settings').first().json.wait_seconds }}", "unit": "seconds"}, (1760, 300),
      webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, "rcp-telegram-wait")))
http(w, "Check recipe", (1980, 300), "GET", REST + "/import_jobs", "supabase",
     query={"id": "=eq.{{ $json.job_id }}", "select": "id,status,images,texts"}, headers=OBJ)
code(w, "Was that the last one?", (2200, 300), r"""
const before = $('Wait for more').first().json;
const now = $json;
const last = now.status === 'collecting'
  && (now.images || []).length === before.image_count
  && (now.texts || []).length === before.text_count;
const n = (now.images || []).length;
const t = (now.texts || []).length;
const parts = [];
if (n) parts.push(`${n} screenshot${n === 1 ? '' : 's'}`);
if (t) parts.push('some text');
return [{ json: { last, job_id: now.id, summary: parts.join(' and ') } }];
""")
if_true(w, "Nothing new arrived?", (2420, 300), "={{ $json.last }}")
tg_send(w, "Ask if that's everything", (2640, 300), "={{ $('Understand message').first().json.chat_id }}",
        "=Got {{ $json.summary }} for one recipe. 📸\n\nIf the recipe continues, send the rest now. When that's everything, tap Done.",
        buttons=[[("✅ Done – add recipe", "=go:{{ $json.job_id }}")], [("✖️ Start over", "=x:{{ $json.job_id }}")]])

# --- buttons
w.add("Answer button", "n8n-nodes-base.telegram", 1.2,
      {"resource": "callback", "operation": "answerQuery", "queryId": "={{ $json.cb_id }}", "additionalFields": {}},
      (880, 620), CRED["telegram"], onError="continueRegularOutput")
switch_n(w, "Which button?", (1100, 620), 2, "={{ $('Understand message').first().json.button }}")
http(w, "Send for reading", (1320, 560), "PATCH", REST + "/import_jobs", "supabase",
     query={"id": "=eq.{{ $('Understand message').first().json.job_id }}", "status": "in.(collecting,error)",
            "attempts": "lt.3"},
     headers={"Prefer": "return=representation"}, body='={{ JSON.stringify({ status: "pending" }) }}',
     alwaysOutputData=True)
if_true(w, "Was it waiting?", (1540, 560), "={{ !!$json.id }}")
tg_send(w, "Say on it", (1760, 500), "={{ $('Understand message').first().json.chat_id }}",
        "=On it! 👩‍🍳 Reading your recipe now. I'll message you when it's in the app's inbox (usually under a minute).")
tg_send(w, "Say already sent", (1760, 640), "={{ $('Understand message').first().json.chat_id }}",
        "=That recipe was already sent, or it has failed 3 times. ✔️ Send new screenshots to start again.")
http(w, "Cancel recipe", (1320, 760), "PATCH", REST + "/import_jobs", "supabase",
     query={"id": "=eq.{{ $('Understand message').first().json.job_id }}", "status": "in.(collecting,error)"},
     headers={"Prefer": "return=representation"}, body='={{ JSON.stringify({ status: "cancelled" }) }}',
     alwaysOutputData=True)
tg_send(w, "Say cleared", (1540, 760), "={{ $('Understand message').first().json.chat_id }}",
        "=Cleared. 🧹 Send screenshots whenever you're ready.")

# --- help / unsupported
tg_send(w, "Help", (880, 860), "={{ $json.chat_id }}",
        "=Hi! Send me screenshots of a recipe (one recipe at a time). If it needs several screenshots, send them all, then tap Done. You can also paste the recipe text.\n\nIt will appear in the app's Inbox for you to check.")
tg_send(w, "Screenshot failed", (1540, 80), "={{ $('Understand message').first().json.chat_id }}",
        "=Sorry, I couldn't save that screenshot. Please send it again. 🙏")
tg_send(w, "Unsupported", (880, 1000), "={{ $json.chat_id }}",
        "=I can only read screenshots sent as photos, or text. Try sending it again as a photo. 🙂")

w.chain("Telegram", "Settings", "Understand message", "Route")
w.link("Route", "Answer button", 0)
w.link("Route", "Is screenshot?", 1)
w.link("Route", "Help", 2)
w.link("Route", "Unsupported", 3)
w.link("Is screenshot?", "Download screenshot", 0)
w.link("Is screenshot?", "Add text to recipe", 1)
w.chain("Download screenshot", "Upload screenshot", "Add screenshot to recipe", "Wait for more")
w.link("Download screenshot", "Screenshot failed", 1)
w.link("Upload screenshot", "Screenshot failed", 1)
w.link("Add text to recipe", "Wait for more")
w.chain("Wait for more", "Check recipe", "Was that the last one?", "Nothing new arrived?")
w.link("Nothing new arrived?", "Ask if that's everything", 0)
w.link("Answer button", "Which button?")
w.link("Which button?", "Send for reading", 0)
w.link("Which button?", "Cancel recipe", 1)
w.link("Send for reading", "Was it waiting?")
w.link("Was it waiting?", "Say on it", 0)
w.link("Was it waiting?", "Say already sent", 1)
w.link("Cancel recipe", "Say cleared")
w.dump("2-telegram-bot.json")

# =====================================================================
# 3. Process job: called by Supabase when a job becomes 'pending'
#    (app upload, Telegram "Done", Retry) + a 10-minute safety sweep
# =====================================================================
w = WF("Recipes · 3 Process job", WF_PROCESS)
w.add("From Supabase", "n8n-nodes-base.webhook", 2,
      {"httpMethod": "POST", "path": "recipes-job", "authentication": "headerAuth",
       "responseMode": "onReceived", "options": {}}, (0, 200), CRED["jobsecret"],
      webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, "rcp-process-webhook")))
w.add("Every 10 minutes", "n8n-nodes-base.scheduleTrigger", 1.2,
      {"rule": {"interval": [{"field": "minutes", "minutesInterval": 10}]}}, (0, 420))
settings(w, (220, 300), {"supabase_url": SUPABASE_URL})
code(w, "Which job", (440, 200), r"""
const body = $('From Supabase').first().json.body || {};
const id = String(body.job_id || '');
if (!/^[0-9a-f-]{36}$/i.test(id)) return [];
return [{ json: { id } }];
""")
http(w, "Find stuck jobs", (440, 420), "POST", REST + "/rpc/sweep_jobs", "supabase", body="={{ JSON.stringify({}) }}")
http(w, "Claim job", (660, 300), "POST", REST + "/rpc/claim_job", "supabase",
     body='={{ JSON.stringify({ p_job_id: $json.id, p_from: ["pending"] }) }}')
if_true(w, "Claimed?", (880, 300), "={{ !!$json.id }}")
exec_wf(w, "Read recipe", (1100, 300), WF_EXTRACT, EXTRACT_NAME,
        {"job_id": "={{ $json.id }}"}, {"job_id": "string"}, **ERR)
code(w, "Crash details", (1540, 520), r"""
const job = $('Claim job').first(0).json;
return [{ json: { ok: false, job_id: job.id, tg_chat_id: job.tg_chat_id ?? '', title: '',
                  error: 'Something went wrong while reading it. Tap Retry.' } }];
""")
http(w, "Mark crashed job failed", (1760, 520), "POST", REST + "/rpc/fail_job", "supabase",
     body="={{ JSON.stringify({ p_job_id: $json.job_id, p_error: $json.error }) }}", onError="continueRegularOutput")
code(w, "Pass on", (1980, 520), r"""
return [{ json: $('Crash details').first().json }];
""")
if_true(w, "From Telegram?", (1320, 300), "={{ !!$json.tg_chat_id }}")
if_true(w, "Worked?", (1540, 300), "={{ $json.ok }}")
tg_send(w, "Tell success", (1760, 220), "={{ $json.tg_chat_id }}",
        "=✅ Added \"" + esc("$json.title") + "\" to the inbox.\n\nOpen the app → Inbox to check it and approve it.")
tg_send(w, "Tell failure", (1760, 400), "={{ $json.tg_chat_id }}",
        "=❌ I couldn't read that recipe.\n" + esc("$json.error") + "\n\nYou can tap Retry, or send clearer screenshots.",
        buttons=[[("🔁 Retry", "=go:{{ $json.job_id }}")]])
w.link("From Supabase", "Settings")
w.link("Every 10 minutes", "Settings")
code(w, "Which trigger", (440, 300), r"""
return [{ json: { from_webhook: $('From Supabase').isExecuted } }];
""")
if_true(w, "Called by Supabase?", (660, 120), "={{ $json.from_webhook }}")
# Settings -> Which trigger -> (webhook) Which job | (schedule) Find stuck jobs
w.link("Settings", "Which trigger")
w.link("Which trigger", "Called by Supabase?")
w.link("Called by Supabase?", "Which job", 0)
w.link("Called by Supabase?", "Find stuck jobs", 1)
w.link("Which job", "Claim job")
w.link("Find stuck jobs", "Claim job")
w.chain("Claim job", "Claimed?")
w.link("Claimed?", "Read recipe", 0)
w.link("Read recipe", "From Telegram?")
w.link("Read recipe", "Crash details", 1)
w.chain("Crash details", "Mark crashed job failed", "Pass on", "From Telegram?")
w.link("From Telegram?", "Worked?", 0)
w.link("Worked?", "Tell success", 0)
w.link("Worked?", "Tell failure", 1)
# tidy positions
for n in w.nodes:
    pos = {"Settings": (220, 300), "Which trigger": (440, 300), "Called by Supabase?": (660, 300),
           "Which job": (880, 200), "Find stuck jobs": (880, 420), "Claim job": (1100, 300),
           "Claimed?": (1320, 300), "Read recipe": (1540, 300), "From Telegram?": (1760, 300),
           "Worked?": (1980, 300), "Tell success": (2200, 220), "Tell failure": (2200, 400)}.get(n["name"])
    if pos:
        n["position"] = list(pos)
w.dump("3-process-job.json")

# =====================================================================
# 5. Drive import — one recipe (sub-workflow)
# =====================================================================
DRIVE_ONE_NAME = "Recipes · 5 Drive import (one recipe)"
w = WF(DRIVE_ONE_NAME, WF_DRIVE_ONE)
w.add("When called", "n8n-nodes-base.executeWorkflowTrigger", 1.1,
      {"inputSource": "workflowInputs", "workflowInputs": {"values": [
          {"name": "ref", "type": "string"}, {"name": "name", "type": "string"},
          {"name": "files", "type": "array"}]}}, (0, 300))
settings(w, (220, 300), {"supabase_url": SUPABASE_URL, "drive_api": DRIVE_API})
code(w, "One item per screenshot", (440, 300), r"""
const g = $('When called').first().json;
const ext = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif' };
return (g.files || []).map((f, i) => ({ json: {
  file_id: f.id, mime: f.mimeType,
  path: `drive/${g.ref}/${String(i + 1).padStart(2, '0')}-${f.id}.${ext[f.mimeType] || 'jpg'}`,
} }));
""")
http(w, "Download from Drive", (660, 300), "GET",
     "={{ $('Settings').first().json.drive_api }}/drive/v3/files/{{ $json.file_id }}", "drive",
     query={"alt": "media", "supportsAllDrives": "true"}, file_response=True)
http(w, "Upload to Supabase", (880, 300), "POST",
     "=" + SB + "/storage/v1/object/recipe-images/{{ $('One item per screenshot').item.json.path }}", "supabase",
     headers={"Content-Type": "={{ $('One item per screenshot').item.json.mime }}", "x-upsert": "true"},
     binary_field="data")
code(w, "Collect paths", (1100, 300), r"""
const g = $('When called').first().json;
const paths = $('One item per screenshot').all().map(i => i.json.path);
if ($input.all().length !== paths.length) throw new Error('Not every screenshot uploaded');
return [{ json: { source: 'drive', status: 'processing', images: paths, source_ref: g.ref, texts: [] } }];
""")
http(w, "Create import job", (1320, 300), "POST", REST + "/import_jobs", "supabase",
     headers={"Prefer": "return=representation", **OBJ}, body="={{ JSON.stringify($json) }}")
exec_wf(w, "Read recipe", (1540, 300), WF_EXTRACT, EXTRACT_NAME,
        {"job_id": "={{ $json.id }}"}, {"job_id": "string"})
w.chain("When called", "Settings", "One item per screenshot", "Download from Drive", "Upload to Supabase",
        "Collect paths", "Create import job", "Read recipe")
w.dump("5-drive-import-one.json")

# =====================================================================
# 4. Drive import — whole folder (run manually)
# =====================================================================
w = WF("Recipes · 4 Drive import (run me)", WF_DRIVE)
w.add("Start import", "n8n-nodes-base.manualTrigger", 1, {}, (0, 300))
settings(w, (220, 300), {"supabase_url": SUPABASE_URL, "drive_api": DRIVE_API,
                         "drive_folder_id": "FOLDER123" if TEST else "PASTE-THE-DRIVE-FOLDER-ID", "max_recipes": 5, "pause_seconds": 3})
http(w, "List folder", (440, 300), "GET", "={{ $json.drive_api }}/drive/v3/files", "drive",
     query={"q": "='{{ $json.drive_folder_id }}' in parents and trashed = false",
            "fields": "files(id,name,mimeType),nextPageToken", "pageSize": "1000", "orderBy": "name",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
GROUP_JS = r"""
const IMG = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
const FOLDER = 'application/vnd.google-apps.folder';
const files = $json.files || [];
const out = [];
for (const f of files) {
  if (f.mimeType === FOLDER) out.push({ json: { ref: f.id, name: f.name, kind: 'folder', files: [] } });
  else if (IMG.includes(f.mimeType)) out.push({ json: { ref: f.id, name: f.name, kind: 'file', files: [f] } });
}
return out;
"""
code(w, "One recipe per file or subfolder", (660, 300), GROUP_JS)
if_true(w, "Is a subfolder?", (880, 300), "={{ $json.kind === 'folder' }}")
http(w, "List subfolder", (1100, 200), "GET", "={{ $('Settings').first().json.drive_api }}/drive/v3/files", "drive",
     query={"q": "='{{ $json.ref }}' in parents and trashed = false",
            "fields": "files(id,name,mimeType)", "pageSize": "100", "orderBy": "name",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
code(w, "Subfolder screenshots", (1320, 200), r"""
const IMG = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
const groups = $('Is a subfolder?').all(0);
return $input.all().map((it, i) => {
  const g = groups[i].json;
  const files = (it.json.files || []).filter(f => IMG.includes(f.mimeType))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  return { json: { ...g, files } };
}).filter(x => x.json.files.length > 0);
""")
w.add("All recipes", "n8n-nodes-base.merge", 3, {}, (1540, 300))
code(w, "Collect refs", (1760, 300), r"""
return [{ json: { refs: $input.all().map(i => i.json.ref) } }];
""")
http(w, "Already imported?", (1980, 300), "POST", REST + "/rpc/known_source_refs", "supabase",
     body="={{ JSON.stringify({ p_refs: $json.refs }) }}", alwaysOutputData=True)
code(w, "Only new ones", (2200, 300), r"""
const known = new Set($input.all().map(i => i.json.source_ref).filter(Boolean));
const max = Number($('Settings').first().json.max_recipes) || 5;
const todo = $('All recipes').all().map(i => i.json).filter(g => !known.has(g.ref));
console.log(`${todo.length} new recipe(s) found; importing up to ${max} this run.`);
return todo.slice(0, max).map(g => ({ json: g }));
""")
w.add("One at a time", "n8n-nodes-base.splitInBatches", 3, {"batchSize": 1, "options": {}}, (2420, 300))
exec_wf(w, "Import one recipe", (2640, 400), WF_DRIVE_ONE, DRIVE_ONE_NAME,
        {"ref": "={{ $json.ref }}", "name": "={{ $json.name }}", "files": "={{ $json.files }}"},
        {"ref": "string", "name": "string", "files": "array"}, onError="continueRegularOutput")
w.add("Pause", "n8n-nodes-base.wait", 1.1,
      {"amount": "={{ $('Settings').first().json.pause_seconds }}", "unit": "seconds"}, (2860, 400),
      webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, "rcp-drive-pause")))
code(w, "Summary", (2640, 200), r"""
// The loop's "done" output carries every recipe's result
const results = $input.all().map(i => i.json);
const IMG = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
const skipped = ($('List folder').first().json.files || [])
  .filter(f => f.mimeType !== 'application/vnd.google-apps.folder' && !IMG.includes(f.mimeType))
  .map(f => f.name);
const ok = results.filter(r => r.ok === true);
const failed = results.filter(r => r.ok !== true);
return [{ json: {
  imported: ok.length,
  failed: failed.length,
  titles: ok.map(r => r.title),
  problems: failed.map(r => r.error || 'unknown error'),
  skipped_files: skipped.length ? skipped.join(', ') + ' (only PNG/JPG screenshots can be read)' : 'none',
  next: 'Check the Inbox in the app. Run again to import the next batch.',
} }];
""")
w.chain("Start import", "Settings", "List folder", "One recipe per file or subfolder", "Is a subfolder?")
w.link("Is a subfolder?", "List subfolder", 0)
w.link("Is a subfolder?", "All recipes", 1, 1)
w.link("List subfolder", "Subfolder screenshots")
w.link("Subfolder screenshots", "All recipes", 0, 0)
w.chain("All recipes", "Collect refs", "Already imported?", "Only new ones", "One at a time")
w.link("One at a time", "Summary", 0)
w.link("One at a time", "Import one recipe", 1)
w.chain("Import one recipe", "Pause")
w.link("Pause", "One at a time")
w.dump("4-drive-import.json")

print("built", "test" if TEST else "production", "workflows")
