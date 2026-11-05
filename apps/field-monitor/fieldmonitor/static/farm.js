/* Farm & team: name the farm, add people with a role, and see which fields are on it.
   User-provided text is always set with textContent. */
(() => {
  "use strict";
  const FM = window.FM;
  const { el, api } = FM;
  const $ = (id) => document.getElementById(id);
  let roleInfo = null;
  let current = null; // selected farm id

  const msgLine = () => el("p", { class: "small", role: "status" });

  FM.views.farm = async (farmId) => {
    const p = $("view-farm");
    p.replaceChildren(el("p", { class: "muted", text: "Loading…" }));
    const [farms, roles, fields] = await Promise.all([api("/api/farms"), roleInfo || api("/api/farms/roles"), api("/api/fields?include_archived=true")]);
    roleInfo = roles;
    current = farms.some((f) => f.id === farmId) ? farmId : (farms.some((f) => f.id === current) ? current : farms[0].id);
    const farm = farms.find((f) => f.id === current);
    const isOwner = farm.my_role === "owner";
    const fieldsById = Object.fromEntries(fields.map((f) => [f.id, f]));
    p.replaceChildren();

    // Header: farm picker (if several) and name
    const head = el("div", { class: "page-head" }, el("h1", { text: farm.name }),
      el("span", { class: "muted", text: `Your role: ${farm.my_role}` }));
    p.append(head);
    if (farms.length > 1) {
      const pick = el("select", { "aria-label": "Farm" }, farms.map((f) => el("option", { value: f.id, text: `${f.name} (${f.my_role})`, ...(f.id === farm.id ? { selected: "" } : {}) })));
      pick.addEventListener("change", () => { location.hash = `#/farm/${encodeURIComponent(pick.value)}`; });
      p.append(el("label", { class: "small" }, "Farm ", pick));
    }

    // Team
    const team = el("section", { class: "card stack" }, el("h2", { text: "Team" }));
    const list = el("div", { class: "team-list" });
    const tmsg = msgLine();
    const renderTeam = (members) => {
      list.replaceChildren();
      for (const m of members) {
        const row = el("div", { class: "team-row" },
          el("div", { class: "team-who" }, el("b", { text: m.display_name || m.email }), el("span", { class: "muted small", text: m.email })));
        if (isOwner) {
          const sel = el("select", { "aria-label": `Role for ${m.display_name || m.email}` },
            roleInfo.roles.map((r) => el("option", { value: r, text: r, ...(r === m.role ? { selected: "" } : {}) })));
          sel.addEventListener("change", async () => {
            try { renderTeam(await api(`/api/farms/${farm.id}/members`, { method: "POST", body: JSON.stringify({ email: m.email, role: sel.value }) })); tmsg.textContent = "Role updated."; }
            catch (err) { tmsg.textContent = err.message; sel.value = m.role; }
          });
          const rm = el("button", { type: "button", class: "btn ghost small-btn", text: "Remove" });
          rm.addEventListener("click", async () => {
            if (!confirm(`Remove ${m.display_name || m.email} from ${farm.name}?`)) return;
            try { renderTeam(await api(`/api/farms/${farm.id}/members/${m.user_id}`, { method: "DELETE" })); tmsg.textContent = "Removed."; }
            catch (err) { tmsg.textContent = err.message; }
          });
          row.append(sel, rm);
        } else {
          row.append(el("span", { class: "chip", text: m.role }));
        }
        list.append(row);
      }
    };
    renderTeam(await api(`/api/farms/${farm.id}/members`));
    team.append(list);
    if (isOwner) {
      const email = el("input", { type: "email", required: "", placeholder: "their account email", autocomplete: "off" });
      const role = el("select", {}, roleInfo.roles.filter((r) => r !== "owner").concat("owner").map((r) => el("option", { value: r, text: r, ...(r === "scout" ? { selected: "" } : {}) })));
      const form = el("form", { class: "row gap wrap end" }, el("label", { class: "grow" }, "Add someone", email), el("label", {}, "Role", role),
        el("button", { class: "btn primary", type: "submit", text: "Add" }));
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        try { renderTeam(await api(`/api/farms/${farm.id}/members`, { method: "POST", body: JSON.stringify({ email: email.value, role: role.value }) })); email.value = ""; tmsg.textContent = "Added."; }
        catch (err) { tmsg.textContent = err.message; }
      });
      team.append(form);
    }
    team.append(tmsg, el("details", { class: "small" }, el("summary", { text: "What the roles mean" }),
      el("ul", { class: "bullets" }, roleInfo.roles.map((r) => el("li", { text: roleInfo.labels[r] }))),
      el("p", { class: "muted", text: "For now owners manage the farm and everyone else can see its fields. What each role can change comes next." })));
    p.append(team);

    // Fields on this farm
    const fsec = el("section", { class: "card stack" }, el("h2", { text: `Fields (${farm.field_ids.length})` }));
    if (!farm.field_ids.length) fsec.append(el("p", { class: "muted", text: "No fields on this farm yet." }));
    const owned = farms.filter((f) => f.my_role === "owner" && f.id !== farm.id);
    for (const fid of farm.field_ids) {
      const f = fieldsById[fid];
      const row = el("div", { class: "team-row" },
        el("div", { class: "team-who" }, el("a", { href: `#/fields/${encodeURIComponent(fid)}`, text: f ? f.name : fid }),
          el("span", { class: "muted small", text: f ? `${FM.fmt(f.area_ac, 0)} ac${f.crop ? " · " + f.crop : ""}${f.archived ? " · archived" : ""}` : "" })));
      if (isOwner && owned.length) {
        const mv = el("select", { "aria-label": "Move to farm" }, el("option", { value: "", text: "Move to…" }), owned.map((o) => el("option", { value: o.id, text: o.name })));
        mv.addEventListener("change", async () => {
          if (!mv.value) return;
          await api(`/api/farms/${mv.value}/fields`, { method: "POST", body: JSON.stringify({ field_ids: [fid] }) });
          FM.views.farm(farm.id);
        });
        row.append(mv);
      }
      fsec.append(row);
    }
    p.append(fsec);

    // Owner settings: rename, add another farm
    if (isOwner) {
      const name = el("input", { type: "text", value: farm.name, maxlength: "120", required: "" });
      const rn = el("form", { class: "row gap wrap end" }, el("label", { class: "grow" }, "Farm name", name), el("button", { class: "btn", type: "submit", text: "Save" }));
      const smsg = msgLine();
      rn.addEventListener("submit", async (e) => { e.preventDefault(); try { await api(`/api/farms/${farm.id}`, { method: "PATCH", body: JSON.stringify({ name: name.value }) }); FM.views.farm(farm.id); } catch (err) { smsg.textContent = err.message; } });
      const nn = el("input", { type: "text", placeholder: "e.g. Rented ground", maxlength: "120", required: "" });
      const nf = el("form", { class: "row gap wrap end" }, el("label", { class: "grow" }, "Add another farm", nn), el("button", { class: "btn", type: "submit", text: "Create" }));
      nf.addEventListener("submit", async (e) => { e.preventDefault(); try { const f = await api("/api/farms", { method: "POST", body: JSON.stringify({ name: nn.value }) }); location.hash = `#/farm/${encodeURIComponent(f.id)}`; } catch (err) { smsg.textContent = err.message; } });
      p.append(el("section", { class: "card stack" }, el("h2", { text: "Settings" }), rn, nf, smsg));
    }
  };
})();
