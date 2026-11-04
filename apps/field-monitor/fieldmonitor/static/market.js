/* Accounts, marketplace, messages, and profiles. User-provided text is always set with textContent. */
(() => {
  "use strict";
  const FM = window.FM;
  const { el, api } = FM;
  const $ = (id) => document.getElementById(id);
  let meta = null; // listing kinds, categories, role labels
  let authCfg = null;

  const initials = (name) => (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
  const when = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    const days = (Date.now() - d) / 864e5;
    return days < 1 ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : days < 7 ? `${Math.floor(days)} d ago` : d.toLocaleDateString();
  };
  async function getMeta() { return meta || (meta = await api("/api/market/meta")); }
  const avatar = (name, cls = "") => el("span", { class: `avatar ${cls}`, text: initials(name), "aria-hidden": "true" });
  const chip = (text, cls = "") => el("span", { class: `chip ${cls}`, text });
  const page = (id) => { const p = $(id); p.replaceChildren(); return p; };
  const field = (label, input) => el("label", {}, label, input);

  // ---------- session -----------------------------------------------------------
  FM.setMe = (me) => {
    FM.state.me = me;
    if (!me) return;
    $("avatar").textContent = initials(me.profile.display_name);
    const b = $("unread-badge");
    b.hidden = !me.unread;
    b.textContent = me.unread > 9 ? "9+" : String(me.unread || "");
  };
  setInterval(async () => {
    if (!FM.state.me || document.hidden) return;
    try { FM.setMe(await api("/api/me")); } catch (_) { /* logged out or offline */ }
  }, 60000);

  // ---------- auth --------------------------------------------------------------
  let authRender = 0;
  FM.views.auth = async () => {
    const mine = ++authRender;
    authCfg = authCfg || (await api("/api/auth/config"));
    if (mine !== authRender) return; // a newer render started while we waited
    const p = page("view-auth");
    const err = new URLSearchParams(location.hash.split("?")[1] || "").get("error");
    const msg = el("p", { class: "small err", role: "alert", text: err ? "Google sign-in didn't work. Try again or use email." : "" });
    let mode = "login";
    const card = el("div", { class: "auth-card card" });
    const render = () => {
      card.replaceChildren();
      card.append(el("h1", { text: FM.appName || "Overacre" }), el("p", { class: "muted", text: FM.tagline || "Satellite field monitoring and a marketplace for ag businesses." }));
      card.append(el("div", { class: "seg" },
        el("button", { type: "button", class: mode === "login" ? "on" : "", text: "Log in", onclick: () => { mode = "login"; render(); } }),
        el("button", { type: "button", class: mode === "signup" ? "on" : "", text: "Sign up", onclick: () => { mode = "signup"; render(); } })));
      const email = el("input", { type: "email", required: "", autocomplete: "email", maxlength: "320" });
      const pw = el("input", { type: "password", required: "", minlength: mode === "signup" ? "10" : "1", autocomplete: mode === "signup" ? "new-password" : "current-password", maxlength: "200" });
      const form = el("form", { class: "stack" });
      let name = null;
      const roleBoxes = [];
      if (mode === "signup") {
        name = el("input", { type: "text", required: "", maxlength: "80", autocomplete: "name" });
        form.append(field("Your name", name));
      }
      form.append(field("Email", email), field(mode === "signup" ? "Password (10+ characters)" : "Password", pw));
      if (mode === "signup") {
        const roles = el("fieldset", { class: "roles" }, el("legend", { text: "I am a… (pick any)" }));
        for (const [key, label] of Object.entries(authCfg.roles)) {
          const cb = el("input", { type: "checkbox", value: key });
          if (key === "farmer") cb.checked = true;
          roleBoxes.push(cb);
          roles.append(el("label", { class: "check" }, cb, label));
        }
        form.append(roles);
      }
      form.append(el("button", { class: "btn primary block", type: "submit", text: mode === "signup" ? "Create account" : "Log in" }), msg);
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        msg.textContent = "";
        try {
          const body = mode === "signup"
            ? { name: name.value, email: email.value, password: pw.value, roles: roleBoxes.filter((c) => c.checked).map((c) => c.value) }
            : { email: email.value, password: pw.value };
          const me = await api(mode === "signup" ? "/api/auth/register" : "/api/auth/login", { method: "POST", body: JSON.stringify(body) });
          FM.setMe(me);
          location.hash = "#/today";
        } catch (ex) { msg.textContent = ex.message; }
      });
      card.append(form);
      if (authCfg.google) {
        card.append(el("div", { class: "or", text: "or" }), el("a", { class: "btn block google", href: "/api/auth/google/start", text: "Continue with Google" }));
      }
    };
    render();
    p.append(card);
  };

  // ---------- marketplace -------------------------------------------------------
  function listingCard(l) {
    return el("a", { class: "lcard card", href: `#/market/${encodeURIComponent(l.id)}` },
      el("div", { class: "row space" }, chip(l.kind_label, `k-${l.kind}`), el("span", { class: "muted small", text: when(l.created) })),
      el("h3", { text: l.title }),
      el("div", { class: "lmeta" }, l.price ? el("b", { text: l.price }) : null, l.location ? el("span", { class: "muted", text: `📍 ${l.location}` }) : null),
      el("p", { class: "ldesc", text: l.description }),
      el("div", { class: "seller small" }, avatar(l.seller_name, "sm"), el("span", { text: l.seller_business || l.seller_name }), el("span", { class: "muted", text: ` · ${l.category}` })));
  }

  FM.views.market = async (arg) => {
    const m = await getMeta();
    if (arg === "new") return newListing(m);
    if (arg) return listingDetail(arg, m);
    const p = page("view-market");
    const q = el("input", { type: "search", placeholder: "Search listings (e.g. drone spraying, seed corn)", maxlength: "80" });
    const kind = el("select", {}, el("option", { value: "", text: "All types" }), ...Object.entries(m.kinds).map(([k, v]) => el("option", { value: k, text: v })));
    const cat = el("select", {}, el("option", { value: "", text: "All categories" }), ...m.categories.map((c) => el("option", { value: c, text: c })));
    const mine = el("input", { type: "checkbox" });
    const grid = el("div", { class: "lgrid" });
    const load = async () => {
      const qs = new URLSearchParams();
      if (q.value.trim()) qs.set("q", q.value.trim());
      if (kind.value) qs.set("kind", kind.value);
      if (cat.value) qs.set("category", cat.value);
      if (mine.checked) qs.set("mine", "true");
      const items = await api(`/api/listings?${qs}`);
      grid.replaceChildren(...(items.length ? items.map(listingCard)
        : [el("p", { class: "muted", text: "No listings match. Be the first to post one." })]));
    };
    let t;
    q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 300); });
    [kind, cat, mine].forEach((x) => x.addEventListener("change", load));
    p.append(
      el("div", { class: "page-head" }, el("h1", { text: "Marketplace" }), el("a", { class: "btn primary", href: "#/market/new", text: "＋ Post a listing" })),
      el("p", { class: "muted", text: "Services, inputs, equipment, and crops from farms and ag businesses." }),
      el("div", { class: "filters row gap wrap" }, el("div", { class: "grow" }, q), kind, cat, el("label", { class: "check small" }, mine, "My listings")),
      grid);
    load();
  };

  async function newListing(m) {
    const p = page("view-market");
    const kind = el("select", { required: "" }, ...Object.entries(m.kinds).map(([k, v]) => el("option", { value: k, text: v })));
    const cat = el("select", { required: "" }, ...m.categories.map((c) => el("option", { value: c, text: c })));
    const title = el("input", { type: "text", required: "", minlength: "3", maxlength: "120", placeholder: "e.g. Drone spraying, 300 ac/day" });
    const desc = el("textarea", { required: "", minlength: "3", maxlength: "4000", rows: "5", placeholder: "What you offer or need, timing, coverage area, how to book" });
    const price = el("input", { type: "text", maxlength: "80", placeholder: "e.g. $12/ac, $4.10/bu, call for quote" });
    const loc = el("input", { type: "text", maxlength: "120", placeholder: "e.g. Story County, IA", value: (FM.state.me.profile.location || "") });
    const msg = el("p", { class: "small err", role: "alert" });
    const form = el("form", { class: "stack card narrow" },
      el("div", { class: "row gap wrap" }, field("Type", kind), field("Category", cat)),
      field("Title", title), field("Details", desc),
      el("div", { class: "row gap wrap" }, field("Price (optional)", price), field("Location", loc)),
      el("div", { class: "row gap" }, el("button", { class: "btn primary", type: "submit", text: "Post listing" }), el("a", { class: "btn ghost", href: "#/market", text: "Cancel" })),
      msg);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const l = await api("/api/listings", { method: "POST", body: JSON.stringify({ kind: kind.value, category: cat.value, title: title.value, description: desc.value, price: price.value || null, location: loc.value || null }) });
        location.hash = `#/market/${l.id}`;
      } catch (ex) { msg.textContent = ex.message; }
    });
    p.append(el("div", { class: "page-head" }, el("h1", { text: "Post a listing" })), form);
  }

  async function listingDetail(id) {
    const p = page("view-market");
    let l;
    try { l = await api(`/api/listings/${encodeURIComponent(id)}`); }
    catch (ex) { p.append(el("p", { text: ex.message }), el("a", { href: "#/market", text: "← Back to marketplace" })); return; }
    const side = el("div", { class: "card stack" },
      el("a", { class: "seller", href: `#/u/${encodeURIComponent(l.user_id)}` }, avatar(l.seller_name), el("div", {}, el("b", { text: l.seller_business || l.seller_name }), el("div", { class: "muted small", text: l.seller_business ? l.seller_name : "View profile" }))));
    if (l.is_mine) {
      side.append(el("button", { class: "btn", type: "button", text: l.status === "active" ? "Mark as closed" : "Reopen listing", onclick: async () => {
        await api(`/api/listings/${l.id}`, { method: "PATCH", body: JSON.stringify({ status: l.status === "active" ? "closed" : "active" }) });
        listingDetail(id);
      } }));
    } else {
      const body = el("textarea", { rows: "4", maxlength: "4000", required: "", placeholder: "Hi, is this still available?" });
      const out = el("p", { class: "small", role: "status" });
      const form = el("form", { class: "stack" }, field(`Message ${l.seller_name}`, body), el("button", { class: "btn primary", type: "submit", text: "Send message" }), out);
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          const c = await api("/api/conversations", { method: "POST", body: JSON.stringify({ to_user_id: l.user_id, listing_id: l.id, body: body.value }) });
          location.hash = `#/messages/${c.id}`;
        } catch (ex) { out.textContent = ex.message; }
      });
      side.append(form, el("button", { type: "button", class: "linkbtn small", text: "Report this listing", onclick: () => report("listing", l.id) }));
    }
    p.append(
      el("a", { href: "#/market", class: "small", text: "← Marketplace" }),
      el("div", { class: "detail-grid" },
        el("div", { class: "card stack" },
          el("div", { class: "row gap wrap" }, chip(l.kind_label, `k-${l.kind}`), chip(l.category), l.status !== "active" ? chip("Closed", "closed") : null),
          el("h1", { text: l.title }),
          el("div", { class: "lmeta" }, l.price ? el("b", { text: l.price }) : null, l.location ? el("span", { class: "muted", text: `📍 ${l.location}` }) : null, el("span", { class: "muted", text: `Posted ${when(l.created)}` })),
          el("p", { class: "prewrap", text: l.description })),
        side));
  }

  async function report(kind, id) {
    const reason = prompt("What's wrong with it? (spam, scam, offensive…)");
    if (!reason || reason.trim().length < 3) return;
    await api("/api/reports", { method: "POST", body: JSON.stringify({ kind, target_id: id, reason }) });
    alert("Thanks. We'll review it.");
  }

  // ---------- messages ----------------------------------------------------------
  let threadTimer = null;
  FM.views.messages = async (convId) => {
    clearInterval(threadTimer);
    const p = page("view-messages");
    const list = el("div", { class: "convlist" });
    const thread = el("div", { class: "thread card" });
    p.append(el("div", { class: "page-head" }, el("h1", { text: "Messages" })), el("div", { class: `inbox ${convId ? "has-thread" : ""}` }, list, thread));
    const convs = await api("/api/conversations");
    if (!convs.length) list.append(el("p", { class: "muted", text: "No messages yet. Contact someone from the Marketplace." }));
    for (const c of convs) {
      list.append(el("a", { class: `conv ${c.id === convId ? "active" : ""}`, href: `#/messages/${c.id}` },
        avatar(c.other_name, "sm"),
        el("div", { class: "grow" },
          el("div", { class: "row space" }, el("b", { text: c.other_business || c.other_name }), c.unread && c.id !== convId ? el("span", { class: "badge", text: String(c.unread) }) : null),
          c.listing_title ? el("div", { class: "small muted", text: `Re: ${c.listing_title}` }) : null,
          el("div", { class: "small snippet", text: c.last_body || "" }))));
    }
    if (!convId) { thread.append(el("p", { class: "muted", text: "Pick a conversation." })); return; }
    const draw = async () => {
      const t = await api(`/api/conversations/${encodeURIComponent(convId)}/messages`);
      thread.replaceChildren(
        el("div", { class: "thread-head" },
          el("a", { href: "#/messages", class: "only-mobile small", text: "← All" }),
          el("a", { href: `#/u/${encodeURIComponent(t.other.id)}` }, el("b", { text: t.other.business || t.other.name })),
          t.listing ? el("a", { class: "small muted", href: `#/market/${t.listing.id}`, text: `Re: ${t.listing.title}` }) : null),
        el("div", { class: "bubbles" }, ...t.messages.map((m) => el("div", { class: `bubble ${m.sender_id === t.me ? "mine" : ""}` },
          el("div", { class: "prewrap", text: m.body }), el("div", { class: "time", text: when(m.created) })))),
        compose());
      const b = thread.querySelector(".bubbles");
      b.scrollTop = b.scrollHeight;
      FM.setMe(await api("/api/me"));
    };
    const compose = () => {
      const ta = el("textarea", { rows: "2", maxlength: "4000", required: "", placeholder: "Write a message" });
      const f = el("form", { class: "compose" }, ta, el("button", { class: "btn primary", type: "submit", text: "Send" }));
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!ta.value.trim()) return;
        await api(`/api/conversations/${encodeURIComponent(convId)}/messages`, { method: "POST", body: JSON.stringify({ body: ta.value }) });
        draw();
      });
      ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) f.requestSubmit(); });
      return f;
    };
    await draw();
    threadTimer = setInterval(() => { if (location.hash === `#/messages/${convId}` && !document.hidden && !thread.querySelector("textarea").value) draw(); }, 10000);
  };

  function feedItem(it) {
    const card = el("div", { class: `card fitem t-${it.type}` });
    const ago = when(it.time);
    if (it.type === "field") {
      card.append(
        el("div", { class: "head" }, chip(`Your field: ${it.field}`), el("span", { class: "muted", text: ago })),
        el("a", { href: `#/fields/${encodeURIComponent(it.field_id)}`, class: "ftitle" }, el("b", { text: it.title })),
        it.points && it.points.length ? el("ul", { class: "bullets small" }, it.points.map((p) => el("li", { text: p }))) : null);
      return card;
    }
    if (it.type === "listing") {
      card.append(
        el("div", { class: "head" }, chip("New listing", "k-service"), el("a", { href: `#/u/${encodeURIComponent(it.author_id)}`, text: it.author }), el("span", { class: "muted", text: ago })),
        el("a", { href: `#/market/${encodeURIComponent(it.id)}` }, el("b", { text: it.title })),
        el("div", { class: "lmeta" }, it.price ? el("b", { text: it.price }) : null, it.location ? el("span", { class: "muted", text: `📍 ${it.location}` }) : null),
        el("p", { class: "ldesc", text: it.body }));
      return card;
    }
    const helpful = el("button", { type: "button", class: it.mine ? "on" : "", text: `👍 Helpful${it.helpful ? ` (${it.helpful})` : ""}` });
    helpful.addEventListener("click", async () => {
      const r = await api(`/api/posts/${it.id}/helpful`, { method: "POST" });
      helpful.className = r.mine ? "on" : "";
      helpful.textContent = `👍 Helpful${r.helpful ? ` (${r.helpful})` : ""}`;
    });
    const cmtBox = el("div", { class: "cmts", hidden: "" });
    const cmtBtn = el("button", { type: "button", text: `💬 ${it.comments ? `${it.comments} comment${it.comments === 1 ? "" : "s"}` : "Comment"}` });
    const drawComments = async () => {
      const list = await api(`/api/posts/${it.id}/comments`);
      const input = el("input", { type: "text", maxlength: "2000", placeholder: "Add a comment", required: "" });
      const f = el("form", { class: "row gap" }, el("div", { class: "grow" }, input), el("button", { class: "btn small-btn", type: "submit", text: "Reply" }));
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        await api(`/api/posts/${it.id}/comments`, { method: "POST", body: JSON.stringify({ body: input.value }) });
        drawComments();
      });
      cmtBox.replaceChildren(...list.map((c) => el("div", {}, el("b", { text: `${c.author}: ` }), el("span", { class: "prewrap", text: c.body }))), f);
      cmtBtn.textContent = `💬 ${list.length ? `${list.length} comment${list.length === 1 ? "" : "s"}` : "Comment"}`;
    };
    cmtBtn.addEventListener("click", () => { cmtBox.hidden = !cmtBox.hidden; if (!cmtBox.hidden) drawComments(); });
    card.append(
      el("div", { class: "head" },
        avatar(it.author, "sm"),
        el("a", { href: `#/u/${encodeURIComponent(it.author_id)}`, text: it.author || "Member" }),
        it.type === "report" ? chip(`Field report · ${it.severity}`, "closed") : null,
        el("span", { class: "muted", text: ago + (it.miles != null ? ` · ${it.miles} mi away` : "") })),
      el("p", { class: "prewrap", text: it.body }),
      el("div", { class: "actions" }, helpful, cmtBtn,
        el("button", { type: "button", text: "Report", onclick: () => report("post", it.id) })),
      cmtBox);
    return card;
  }

  // ---------- news ----------------------------------------------------------------
  const SECTIONS = [["All", "all"], ["Local", "local"], ["National", "national"], ["Community", "community"], ["Marketplace", "market"], ["My fields", "fields"]];
  let section = "all";
  const money = (v) => `$${v.toFixed(2)}`;
  const cents = (v) => (Math.abs(v) < 1 ? `${Math.round(Math.abs(v) * 100)}¢` : `$${Math.abs(v).toFixed(2)}`);

  function sparkline(values) {
    if (!values || values.length < 2) return null;
    const w = 90, h = 26, lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - 2 - ((v - lo) / span) * (h - 4)}`).join(" ");
    const s = FM.svg("svg", { viewBox: `0 0 ${w} ${h}`, class: "spark", "aria-hidden": "true" });
    s.append(FM.svg("polyline", { points: pts }));
    return s;
  }

  function priceCard(c) {
    if (c.price == null) {
      return el("div", { class: "pcard empty" }, el("div", { class: "pname", text: c.name }),
        el("div", { class: "pprice muted", text: "—" }), el("div", { class: "pmeta", text: "No price yet" }));
    }
    const dir = c.change > 0 ? "up" : c.change < 0 ? "down" : "flat";
    const arrow = dir === "up" ? "▲" : dir === "down" ? "▼" : "■";
    const label = c.kind === "futures" ? `Futures${c.contract ? " " + c.contract : ""}` : `Cash bid${c.contract ? ` (${c.contract})` : ""}`;
    return el("div", { class: `pcard ${dir}`, title: `${c.name}: ${money(c.price)}/${c.unit} · ${label} · ${c.source} · ${c.as_of}` },
      el("div", { class: "row space" }, el("span", { class: "pname", text: c.name }), sparkline(c.history)),
      el("div", { class: "pprice" }, money(c.price), el("span", { class: "punit", text: `/${c.unit}` })),
      c.change != null ? el("div", { class: `pchg ${dir}`, text: `${arrow} ${cents(c.change)}${c.pct != null ? ` (${Math.abs(c.pct).toFixed(1)}%)` : ""}` }) : null,
      el("div", { class: "pmeta", text: `${label} · ${c.source} · ${when(c.as_of)}` }));
  }

  async function priceBoard(box) {
    const b = await api("/api/prices");
    box.replaceChildren(el("div", { class: "pboard" }, ...b.commodities.map(priceCard)));
    const me = FM.state.me;
    if (me && me.user.is_admin) {
      const com = el("select", {}, ...b.commodities.map((c) => el("option", { value: c.key, text: c.name })));
      const price = el("input", { type: "number", step: "0.01", min: "0.01", max: "99", required: "", placeholder: "4.32" });
      const kind = el("select", {}, el("option", { value: "cash", text: "Cash bid" }), el("option", { value: "futures", text: "Futures" }));
      const src = el("input", { type: "text", required: "", maxlength: "80", placeholder: "e.g. Chariton Co-op" });
      const msg = el("span", { class: "small", role: "status" });
      const form = el("form", { class: "row gap wrap end" }, field("Crop", com), field("Price ($/bu)", price), field("Type", kind), field("Source", src),
        el("button", { class: "btn", type: "submit", text: "Save" }), msg);
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          await api("/api/prices", { method: "POST", body: JSON.stringify({ commodity: com.value, price: Number(price.value), kind: kind.value, source: src.value }) });
          priceBoard(box);
        } catch (ex) { msg.textContent = ex.message; }
      });
      box.append(el("details", { class: "more small" }, el("summary", { text: b.live_feed ? "Add a local cash bid" : "Update prices (no live feed connected)" }), form));
    }
  }

  function newsItem(it) {
    const ago = when(it.time);
    if (it.type === "roundup") {
      return el("div", { class: "card fitem t-roundup" }, el("div", { class: "head" }, chip("Local roundup"), el("b", { text: it.title })),
        el("ul", { class: "bullets small" }, it.points.map((p) => el("li", { text: p }))));
    }
    if (it.type === "news") {
      return el("a", { class: "card fitem t-news", href: it.url, target: "_blank", rel: "noopener nofollow" },
        el("div", { class: "head" }, chip("National"), el("span", { class: "muted", text: `${it.source} · ${ago}` })),
        el("b", { class: "ftitle", text: it.title }), it.body ? el("p", { class: "ldesc", text: it.body }) : null,
        el("span", { class: "small muted", text: "Read at source ↗" }));
    }
    if (it.type === "storm") {
      return el("div", { class: "card fitem t-storm" },
        el("div", { class: "head" }, chip("Storm report", "closed"), el("span", { class: "muted", text: `${ago}${it.miles != null ? ` · ${it.miles} mi from your fields` : ""}` })),
        el("b", { class: "ftitle", text: it.title }), it.body ? el("p", { class: "small muted", text: it.body }) : null,
        el("span", { class: "small muted", text: `Source: ${it.source} (preliminary)` }));
    }
    const card = feedItem(it);
    if (it.type === "report" && it.title) card.insertBefore(el("b", { class: "ftitle", text: it.title }), card.children[1]);
    return card;
  }

  FM.views.news = async () => {
    const p = page("view-feed");
    const prices = el("div", { class: "prices" }, el("p", { class: "muted small", text: "Loading prices…" }));
    const fields = (FM.state.fields && FM.state.fields.length ? FM.state.fields : await api("/api/fields")).filter((f) => f.my_role === "owner");
    const body = el("textarea", { rows: "2", maxlength: "3000", placeholder: "Share an update: crop progress, what you're seeing, a question for the area…" });
    const where = el("select", {}, el("option", { value: "", text: "No location" }), ...fields.map((f) => el("option", { value: f.id, text: `Near ${f.name}` })));
    const msg = el("p", { class: "small err", role: "alert" });
    const composer = el("form", { class: "card composer" }, body,
      el("div", { class: "row gap wrap end" }, el("label", { class: "small" }, "Tag my area (≈1 km)", where), el("span", { class: "grow" }),
        el("button", { class: "btn primary", type: "submit", text: "Post" })), msg);
    const list = el("div", { class: "stack" });
    const chips = el("div", { class: "chips", role: "tablist" });
    const load = async () => {
      chips.replaceChildren(...SECTIONS.map(([label, key]) => el("button", { type: "button", role: "tab", class: key === section ? "on" : "", text: label,
        "aria-selected": String(key === section), onclick: () => { section = key; load(); } })));
      list.replaceChildren(el("p", { class: "muted small", text: "Loading…" }));
      const items = await api(`/api/feed?section=${section}`);
      const empty = { local: "Nothing reported within 100 miles of your fields this month.", national: "No headlines yet. Sources refresh every 2 hours.",
        fields: "No alerts on your fields.", market: "No new listings." }[section] || "Nothing here yet. Post the first update!";
      list.replaceChildren(...(items.length ? items.map(newsItem) : [el("p", { class: "muted", text: empty })]));
    };
    composer.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!body.value.trim()) return;
      try {
        await api("/api/posts", { method: "POST", body: JSON.stringify({ body: body.value, field_id: where.value || null }) });
        body.value = "";
        section = "all";
        load();
      } catch (ex) { msg.textContent = ex.message; }
    });
    p.append(el("div", { class: "page-head" }, el("h1", { text: "News" })), prices, chips, composer, list);
    priceBoard(prices).catch((ex) => prices.replaceChildren(el("p", { class: "small muted", text: `Prices unavailable: ${ex.message}` })));
    load();
  };

  // ---------- today: the daily brief ------------------------------------------------
  function miniRing(sc) {
    const r = 18, c = 2 * Math.PI * r;
    const s = FM.svg("svg", { viewBox: "0 0 44 44", class: `ring mini ${sc && sc.score != null ? FM.scoreClass(sc.score) : ""}`, role: "img",
      "aria-label": sc && sc.score != null ? `Field score ${sc.score} out of 100` : "No score yet" });
    s.append(FM.svg("circle", { cx: 22, cy: 22, r, class: "track" }));
    if (sc && sc.score != null) s.append(FM.svg("circle", { cx: 22, cy: 22, r, class: "fill", "stroke-dasharray": `${(c * sc.score) / 100} ${c}`, transform: "rotate(-90 22 22)" }));
    const t = FM.svg("text", { x: 22, y: 26, "text-anchor": "middle", class: "num" });
    t.textContent = sc && sc.score != null ? String(sc.score) : "–";
    s.append(t);
    return s;
  }

  function fieldCard(f) {
    const img = f.image;
    const ch = f.change;
    const risk = f.pressure && f.pressure.score != null ? f.pressure : null;
    const health = img
      ? el("div", { class: "fc-health" }, el("b", { text: FM.fmt(img.ndvi) }), el("span", { class: "muted", text: " NDVI (0–1)" }),
          ch ? el("span", { class: `delta ${ch.delta > 0.02 ? "up" : ch.delta < -0.02 ? "down" : "flat"}`,
            text: ` ${ch.delta > 0.02 ? "▲" : ch.delta < -0.02 ? "▼" : "■"} ${Math.abs(ch.delta).toFixed(2)} since ${ch.since}` }) : null)
      : el("div", { class: "fc-health muted", text: "No clear image yet" });
    const trend = f.trend && f.trend.length > 1 ? sparkline(f.trend.map((t) => t.v)) : null;
    const scoreText = f.score && f.score.score != null
      ? `${f.score.label}${f.score.status === "paused" ? ` · final, ${f.score.as_of_label}` : ""}` : "Score after in-season images";
    return el("a", { class: `card fcard2 p${f.priority}`, href: `#/fields/${encodeURIComponent(f.id)}` },
      el("div", { class: "row space" }, el("div", {}, el("b", { class: "fc-name", text: f.name }),
        el("div", { class: "muted small", text: `${FM.fmt(f.area_ac, 0)} ac${f.crop ? " · " + f.crop : ""}${f.role === "viewer" ? " · shared with you" : ""}` })),
        risk ? chip(`Disease: ${risk.level}`, `risk-${risk.level.toLowerCase()}`) : null),
      el("div", { class: "fc-row" }, miniRing(f.score), el("div", { class: "small" }, el("div", { text: scoreText }), health)),
      trend ? el("div", { class: `fc-trend ${ch && ch.delta < -0.02 ? "down" : ""}` }, el("span", { class: "muted small", text: "Season so far" }), trend) : null,
      f.yield && (f.yield.bu_ac != null || f.yield.method === "relative")
        ? el("div", { class: "small fc-yield" }, el("span", { class: "muted", text: `Yield ${f.yield.year}: ` }),
            f.yield.bu_ac != null
              ? el("b", { text: `${Math.round(f.yield.bu_ac)} bu/ac` }) : el("span", { text: f.yield.label }),
            f.yield.bu_ac != null && f.yield.method !== "harvest" ? el("span", { class: "muted", text: ` (${f.yield.low}–${f.yield.high}, ${f.yield.confidence})` }) : null)
        : null,
      el("p", { class: "fc-head", text: f.headline }),
      el("div", { class: `small ${img && !img.fresh ? "stale" : "muted"}`,
        text: img ? (img.fresh ? `Latest image ${img.label} · ${img.age_days === 0 ? "today" : img.age_days === 1 ? "yesterday" : `${img.age_days} days ago`}`
          : `Latest clear image ${img.label}: older than 2 weeks`) : "Imagery is on its way" }));
  }

  FM.views.today = async () => {
    const p = page("view-today");
    p.append(el("p", { class: "muted", text: "Loading your brief…" }));
    const b = await api("/api/brief");
    const hour = new Date().getHours();
    const hello = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    const first = (b.name || "").split(" ")[0];
    p.replaceChildren(
      el("div", { class: "page-head" }, el("h1", { text: `${hello}${first ? ", " + first : ""}` }), el("span", { class: "muted", text: b.date_label })));
    if (!b.fields.length) {
      p.append(el("div", { class: "card stack" }, el("p", { text: "Add your first field to start your daily brief." }),
        el("a", { class: "btn primary", href: "#/fields", text: "＋ Map a field" })));
      return;
    }
    const pr = el("div", { class: "card brief-pri" }, el("h2", { text: "Today's priorities" }));
    if (b.priorities.length) {
      pr.append(el("ol", {}, b.priorities.map((x) => el("li", {}, el("a", { href: `#/fields/${encodeURIComponent(x.field_id)}`, text: x.field }), `: ${x.text}`))));
    } else {
      pr.append(el("p", { class: "ok", text: "✓ Nothing urgent on your fields. Check back tomorrow." }));
    }
    p.append(pr);
    if (b.prices.commodities.some((c) => c.price != null)) p.append(el("div", { class: "pboard compact" }, ...b.prices.commodities.map(priceCard)));
    p.append(el("h2", { text: "Your fields" }), el("div", { class: "fgrid" }, ...b.fields.map(fieldCard)));
    if (b.local.length) {
      const near = el("div", { class: "card stack" }, el("div", { class: "row space" }, el("h2", { text: "Near you this week" }), el("a", { href: "#/news", class: "small", text: "All news →" })));
      for (const it of b.local) {
        if (it.type === "roundup") near.append(el("ul", { class: "bullets small" }, it.points.map((x) => el("li", { text: x }))));
        else near.append(el("div", { class: "small" }, el("b", { text: it.title || it.body }), el("span", { class: "muted", text: ` · ${when(it.time)}${it.miles != null ? ` · ${it.miles} mi away` : ""}` })));
      }
      p.append(near);
    }
    p.append(el("p", { class: "small muted", text: `The brief shows the last ${b.recent_days} days. Older images and history stay on each field's chart and Data tab.` }));
  };

  // ---------- profiles ----------------------------------------------------------
  FM.views.profile = async (userId, editing) => {
    const p = page("view-profile");
    const m = await getMeta();
    const data = await api(`/api/users/${encodeURIComponent(userId)}`);
    const pr = data.profile;
    const head = el("div", { class: "card profile-head" },
      avatar(pr.display_name, "lg"),
      el("div", { class: "grow" },
        el("h1", { text: pr.business_name || pr.display_name }),
        pr.business_name ? el("div", { class: "muted", text: pr.display_name }) : null,
        el("div", { class: "row gap wrap" }, ...pr.role_labels.map((r) => chip(r))),
        pr.location ? el("div", { class: "muted small", text: `📍 ${pr.location}` }) : null),
      data.is_me ? el("a", { class: "btn ghost", href: "#/me", text: "Edit profile" })
        : el("button", { class: "btn primary", type: "button", text: "Message", onclick: async () => {
          const text = prompt(`Message ${pr.display_name}`);
          if (!text) return;
          const c = await api("/api/conversations", { method: "POST", body: JSON.stringify({ to_user_id: pr.id, body: text }) });
          location.hash = `#/messages/${c.id}`;
        } }));
    p.append(head);
    if (pr.bio || pr.website || pr.phone) {
      p.append(el("div", { class: "card stack" },
        pr.bio ? el("p", { class: "prewrap", text: pr.bio }) : null,
        el("div", { class: "row gap wrap small" },
          pr.website ? el("a", { href: pr.website, target: "_blank", rel: "noopener nofollow ugc", text: pr.website.replace(/^https?:\/\//, "") }) : null,
          pr.phone ? el("a", { href: `tel:${pr.phone.replace(/[^\d+]/g, "")}`, text: pr.phone }) : null)));
    }
    if (data.showcase.length) {
      p.append(el("h2", { text: "Fields" }), el("div", { class: "lgrid" }, ...data.showcase.map((f) => el("div", { class: "card fcard" },
        el("div", { class: "row space" }, el("b", { text: f.name }), f.score != null ? el("span", { class: `score-pill ${FM.scoreClass(f.score)}`, text: String(f.score) }) : null),
        el("div", { class: "muted small", text: `${FM.fmt(f.area_ac, 0)} ac${f.crop ? " · " + f.crop : ""}${f.label ? " · " + f.label : ""}${f.as_of ? " · " + f.as_of : ""}` })))));
    }
    p.append(el("h2", { text: data.is_me ? "My listings" : "Listings" }),
      data.listings.length ? el("div", { class: "lgrid" }, ...data.listings.map(listingCard)) : el("p", { class: "muted", text: "No active listings." }));
    if (!data.is_me) p.append(el("button", { type: "button", class: "linkbtn small", text: "Report this profile", onclick: () => report("user", pr.id) }));
    if (editing) p.prepend(editProfile(pr, m));
  };

  function editProfile(pr, m) {
    const inp = (k, attrs = {}) => el("input", { type: "text", value: pr[k] || "", ...attrs });
    const f = {
      display_name: inp("display_name", { required: "", maxlength: "80" }),
      business_name: inp("business_name", { maxlength: "120", placeholder: "Farm or company name" }),
      location: inp("location", { maxlength: "120", placeholder: "County, State" }),
      website: inp("website", { maxlength: "200", placeholder: "example.com" }),
      phone: inp("phone", { maxlength: "40", placeholder: "Shown on your profile" }),
      bio: el("textarea", { rows: "4", maxlength: "2000", placeholder: "What you grow or offer, acres, service area, certifications" }),
    };
    f.bio.value = pr.bio || "";
    const roles = Object.entries(m.roles).map(([k, v]) => {
      const cb = el("input", { type: "checkbox", value: k });
      cb.checked = pr.roles.includes(k);
      return { cb, label: el("label", { class: "check" }, cb, v) };
    });
    const msg = el("p", { class: "small", role: "status" });
    const form = el("form", { class: "card stack" },
      el("h2", { text: "Edit profile" }),
      el("div", { class: "row gap wrap" }, field("Name", f.display_name), field("Business", f.business_name)),
      el("fieldset", { class: "roles" }, el("legend", { text: "Roles" }), ...roles.map((r) => r.label)),
      el("div", { class: "row gap wrap" }, field("Location", f.location), field("Website", f.website), field("Phone", f.phone)),
      field("About", f.bio),
      el("div", { class: "row gap" }, el("button", { class: "btn primary", type: "submit", text: "Save" })), msg);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const body = Object.fromEntries(Object.entries(f).map(([k, x]) => [k, x.value]));
        body.roles = roles.filter((r) => r.cb.checked).map((r) => r.cb.value);
        const saved = await api("/api/me/profile", { method: "PATCH", body: JSON.stringify(body) });
        FM.state.me.profile = saved;
        FM.setMe(FM.state.me);
        msg.textContent = "Saved.";
        FM.views.profile(saved.id, true);
      } catch (ex) { msg.textContent = ex.message; }
    });
    return form;
  }
})();
