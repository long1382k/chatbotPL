(() => {
  const body = document.body;
  const documentId = body.getAttribute("data-document-id") || "";
  const collection = body.getAttribute("data-collection") || "";
  const $ = (sel) => document.querySelector(sel);
  const LS_KEY = "dbm_qdrant_url";

  function setStatus(msg, kind) {
    const node = $("#dbm-pt-status");
    if (!node) return;
    node.textContent = msg || "";
    node.classList.remove("err", "ok");
    if (kind === "err") node.classList.add("err");
    if (kind === "ok") node.classList.add("ok");
  }

  function qdrantUrl() {
    const inp = $("#dbm-qdrant-url");
    return (inp && inp.value.trim()) || null;
  }

  function loadStoredUrl() {
    const inp = $("#dbm-qdrant-url");
    const saved = localStorage.getItem(LS_KEY);
    if (inp && saved && !inp.value.trim()) inp.value = saved;
  }

  function saveStoredUrl() {
    const inp = $("#dbm-qdrant-url");
    if (inp && inp.value.trim()) localStorage.setItem(LS_KEY, inp.value.trim());
  }

  function shortId(id) {
    const s = String(id);
    if (s.length <= 24) return s;
    return `${s.slice(0, 10)}…${s.slice(-8)}`;
  }

  function copyText(text, okMsg) {
    const t = String(text);
    const done = () => setStatus(okMsg || "Đã copy vào clipboard.", "ok");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t).then(done).catch(fallback);
    } else {
      fallback();
    }
    function fallback() {
      const ta = document.createElement("textarea");
      ta.value = t;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        done();
      } catch {
        setStatus("Không copy được (trình duyệt chặn).", "err");
      }
      document.body.removeChild(ta);
    }
  }

  function returnTextFromPayload(pl) {
    if (!pl || typeof pl !== "object") return "";
    const r = (pl.return_text || "").trim();
    if (r) return r;
    const c = pl.content;
    if (c && typeof c === "object" && (c.return_text || "").trim()) return String(c.return_text).trim();
    return "";
  }

  function buildCard(rec) {
    const id = rec.id;
    const pl = rec.payload && typeof rec.payload === "object" ? rec.payload : {};
    const payloadStr = JSON.stringify(pl, null, 2);
    const ret = returnTextFromPayload(pl);

    const art = document.createElement("article");
    art.className = "dbm-point-card";
    art.dataset.pointId = String(id);

    const head = document.createElement("header");
    head.className = "dbm-point-head";

    const idWrap = document.createElement("div");
    idWrap.className = "dbm-point-id-block";
    const lab = document.createElement("span");
    lab.className = "dbm-point-label";
    lab.textContent = "Point";
    const code = document.createElement("code");
    code.className = "dbm-point-id";
    code.title = String(id);
    code.textContent = shortId(id);
    idWrap.appendChild(lab);
    idWrap.appendChild(code);

    const actions = document.createElement("div");
    actions.className = "dbm-point-head-actions";

    const bCopyId = document.createElement("button");
    bCopyId.type = "button";
    bCopyId.className = "dbm-icon-btn";
    bCopyId.innerHTML = '<span class="dbm-ic" aria-hidden="true">⎘</span> Copy id';
    bCopyId.addEventListener("click", () => copyText(String(id), "Đã copy point id."));

    const bDel = document.createElement("button");
    bDel.type = "button";
    bDel.className = "dbm-icon-btn dbm-icon-btn-danger";
    bDel.innerHTML = '<span class="dbm-ic" aria-hidden="true">🗑</span> Xoá';
    bDel.addEventListener("click", () => deletePoint(art, String(id)));

    actions.appendChild(bCopyId);
    actions.appendChild(bDel);
    head.appendChild(idWrap);
    head.appendChild(actions);

    const secPay = document.createElement("section");
    secPay.className = "dbm-point-section";
    const payHead = document.createElement("div");
    payHead.className = "dbm-section-head";
    const payTitle = document.createElement("span");
    payTitle.className = "dbm-section-title";
    payTitle.textContent = "Payload";
    const payActions = document.createElement("div");
    payActions.className = "dbm-section-actions";

    const bCopyPl = document.createElement("button");
    bCopyPl.type = "button";
    bCopyPl.className = "dbm-mini-btn";
    bCopyPl.textContent = "Copy JSON";
    bCopyPl.addEventListener("click", () => copyText(payloadStr, "Đã copy payload JSON."));

    payActions.appendChild(bCopyPl);
    if (ret) {
      const bCopyRet = document.createElement("button");
      bCopyRet.type = "button";
      bCopyRet.className = "dbm-mini-btn";
      bCopyRet.textContent = "Copy return_text";
      bCopyRet.addEventListener("click", () => copyText(ret, "Đã copy return_text."));
      payActions.appendChild(bCopyRet);
    }

    payHead.appendChild(payTitle);
    payHead.appendChild(payActions);

    const pre = document.createElement("pre");
    pre.className = "dbm-payload-pre";
    pre.textContent = payloadStr;

    secPay.appendChild(payHead);
    secPay.appendChild(pre);

    const secVec = document.createElement("section");
    secVec.className = "dbm-point-section dbm-vectors-block";
    const vTitle = document.createElement("div");
    vTitle.className = "dbm-section-head";
    const vt = document.createElement("span");
    vt.className = "dbm-section-title";
    vt.textContent = "Vectors";
    vTitle.appendChild(vt);
    const vp = document.createElement("p");
    vp.className = "dbm-vectors-note";
    vp.textContent =
      collection.includes("dual")
        ? "dense_search · dense_summary — mỗi vector 768 chiều (cosine). Không tải vector lên UI để giữ nhẹ; xem đầy đủ trên Qdrant Dashboard."
        : "dense — 768 chiều. Không tải vector trên UI; xem trên Qdrant Dashboard.";
    secVec.appendChild(vTitle);
    secVec.appendChild(vp);

    art.appendChild(head);
    art.appendChild(secPay);
    art.appendChild(secVec);
    return art;
  }

  async function deletePoint(articleEl, pointId) {
    if (
      !confirm(
        `Xoá point này khỏi collection "${collection}"?\n` + `id: ${pointId}\n` + "Thao tác không hoàn tác.",
      )
    ) {
      return;
    }
    setStatus("Đang xoá point…");
    const qu = qdrantUrl();
    let r;
    let j;
    try {
      r = await fetch("/api/dbmanager/qdrant/delete-point", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          point_id: pointId,
          collection,
          document_id: documentId,
          qdrant_url: qu,
        }),
      });
      j = await r.json();
    } catch (e) {
      setStatus(String(e), "err");
      return;
    }
    if (!r.ok) {
      setStatus(j.detail || "Lỗi xoá", "err");
      return;
    }
    articleEl.remove();
    const list = $("#dbm-pt-list");
    const left = list ? list.querySelectorAll(".dbm-point-card").length : 0;
    if (j.remaining_for_document === 0) {
      setStatus("Đã xoá point cuối — không còn point nào của văn bản trong Qdrant. Registry đã cập nhật.", "ok");
    } else {
      setStatus(`Đã xoá. Còn ${j.remaining_for_document} point cho document_id này.`, "ok");
    }
    if (left === 0 && j.remaining_for_document === 0) {
      const p = document.createElement("p");
      p.className = "hint";
      p.innerHTML = '<a href="/dbmanager">← Quay lại danh sách văn bản</a>';
      if (list) list.appendChild(p);
    }
  }

  async function loadPoints() {
    const list = $("#dbm-pt-list");
    const trunc = $("#dbm-pt-trunc");
    if (!list) return;
    list.innerHTML = "";
    if (trunc) {
      trunc.hidden = true;
      trunc.textContent = "";
    }
    setStatus("Đang gọi QdrantClient.scroll…");

    const qu = qdrantUrl();
    const q = new URLSearchParams({
      document_id: documentId,
      collection,
      limit: "5000",
    });
    if (qu) q.set("qdrant_url", qu);

    let r;
    let j;
    try {
      r = await fetch(`/api/dbmanager/qdrant/point-ids?${q}`);
      j = await r.json();
    } catch (e) {
      setStatus(String(e), "err");
      return;
    }
    if (!r.ok) {
      setStatus(j.detail || "Lỗi tải point", "err");
      return;
    }

    const title = $("#dbm-pt-title");
    const sub = $("#dbm-pt-sub");
    const firstPl = (j.records && j.records[0] && j.records[0].payload) || {};
    const docTitle = (firstPl.title || "").trim();
    if (title) title.textContent = docTitle ? `Points — ${docTitle}` : "Points trong Qdrant";
    if (sub) {
      sub.textContent = `document_id: ${documentId} · collection: ${collection} · ${j.returned}/${j.total_matching} point`;
    }

    if (trunc && j.truncated) {
      trunc.hidden = false;
      trunc.textContent = `Chỉ hiển thị ${j.returned} / ${j.total_matching} point (giới hạn 5000). Tăng limit trên API nếu cần.`;
    }

    for (const rec of j.records || []) {
      list.appendChild(buildCard(rec));
    }
    if (!(j.records || []).length) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "Không có point nào khớp filter (hoặc collection trống).";
      list.appendChild(p);
    }
    setStatus(`Đã tải ${(j.records || []).length} point.`, "ok");
  }

  if (!collection) {
    const miss = $("#dbm-pt-missing");
    const bd = $("#dbm-pt-body");
    if (miss) miss.hidden = false;
    if (bd) bd.hidden = true;
    $("#dbm-pt-title").textContent = "Thiếu tham số";
    $("#dbm-pt-sub").textContent = "";
    return;
  }

  $("#dbm-pt-missing").hidden = true;
  $("#dbm-pt-body").hidden = false;
  $("#dbm-pt-sub").textContent = `document_id: ${documentId} · collection: ${collection}`;

  loadStoredUrl();
  const qp = new URLSearchParams(window.location.search);
  const urlFromQuery = qp.get("qdrant_url");
  const urlInp = $("#dbm-qdrant-url");
  if (urlFromQuery && urlInp) {
    urlInp.value = urlFromQuery;
    localStorage.setItem(LS_KEY, urlFromQuery.trim());
  }
  if (urlInp) {
    urlInp.addEventListener("change", saveStoredUrl);
    urlInp.addEventListener("blur", saveStoredUrl);
  }

  $("#dbm-pt-refresh").addEventListener("click", () => loadPoints().catch((e) => setStatus(String(e), "err")));

  loadPoints().catch((e) => setStatus(String(e), "err"));
})();
