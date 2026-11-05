/* Opening farm survey: a few taps about how this farm usually plants, so reminders fit.
   Questions come from the server (/api/survey/questions). User text is set with textContent. */
(() => {
  "use strict";
  const FM = window.FM;
  const { el, api } = FM;
  const $ = (id) => document.getElementById(id);

  FM.views.survey = async (farmId) => {
    const p = $("view-survey");
    p.replaceChildren(el("p", { class: "muted", text: "Loading…" }));
    const farms = await api("/api/farms");
    const farm = farms.find((f) => f.id === farmId) || farms.find((f) => ["owner", "operator"].includes(f.my_role)) || farms[0];
    const [questions, cur] = await Promise.all([api("/api/survey/questions"), api(`/api/farms/${encodeURIComponent(farm.id)}/survey`)]);
    const answers = { ...((cur.latest && cur.latest.answers) || {}) };
    const canEdit = ["owner", "operator"].includes(farm.my_role);
    const form = el("form", { class: "survey" });
    const msg = el("p", { class: "small", role: "status" });

    const applies = (q) => !q.if || (answers[q.if[0]] || []).includes(q.if[1]);
    function render() {
      form.replaceChildren();
      questions.filter(applies).forEach((q, n) => {
        const card = el("fieldset", { class: "card survey-q", ...(canEdit ? {} : { disabled: "" }) },
          el("legend", { text: `${n + 1}. ${q.text}${q.required ? " *" : ""}` }));
        if (q.type === "multi" || q.type === "single") {
          const opts = el("div", { class: "survey-opts" });
          for (const [value, label] of q.options) {
            const on = q.type === "multi" ? (answers[q.id] || []).includes(value) : answers[q.id] === value;
            const b = el("button", { type: "button", class: `survey-opt${on ? " on" : ""}`, "aria-pressed": String(on), text: label });
            b.addEventListener("click", () => {
              if (q.type === "multi") {
                const set = new Set(answers[q.id] || []);
                set.has(value) ? set.delete(value) : set.add(value);
                answers[q.id] = [...set];
              } else {
                answers[q.id] = answers[q.id] === value ? undefined : value;
              }
              render();
            });
            opts.append(b);
          }
          card.append(opts);
        } else if (q.type === "monthday") {
          const yr = new Date().getFullYear();
          const inp = el("input", { type: "date", value: answers[q.id] ? `${yr}-${answers[q.id]}` : "" });
          inp.addEventListener("change", () => { answers[q.id] = inp.value ? inp.value.slice(5) : undefined; });
          card.append(el("label", { class: "small" }, inp, el("span", { class: "muted", text: " any year; we only use the month and day" })));
        }
        form.append(card);
      });
      if (canEdit) form.append(el("div", { class: "row gap" }, el("button", { class: "btn primary", type: "submit", text: cur.latest ? "Save changes" : "Save" }),
        el("a", { class: "btn ghost", href: "#/today", text: cur.latest ? "Cancel" : "Skip for now" })), msg);
    }
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const clean = Object.fromEntries(Object.entries(answers).filter(([, v]) => v !== undefined && !(Array.isArray(v) && !v.length)));
      try {
        await api(`/api/farms/${encodeURIComponent(farm.id)}/survey`, { method: "PUT", body: JSON.stringify({ answers: clean }) });
        msg.textContent = "Saved. Your reminders now follow how you farm.";
        setTimeout(() => { location.hash = "#/today"; }, 900);
      } catch (err) { msg.textContent = err.message; }
    });
    render();
    p.replaceChildren(
      el("div", { class: "page-head" }, el("h1", { text: "How do you farm?" }), el("span", { class: "muted", text: farm.name })),
      el("p", { class: "muted", text: canEdit
        ? "A few taps so reminders fit how you work: when you usually plant, what tells you it's time, and whether you take stand counts. Change it any time from Farm & team."
        : "The farm's owner or operator fills this in. Here's what they said." }),
      ...(cur.latest ? [el("p", { class: "small muted", text: `Last updated ${new Date(cur.latest.answered_at).toLocaleDateString()}` })] : []),
      form);
  };
})();
