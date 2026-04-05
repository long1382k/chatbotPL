(() => {
  const $ = (sel) => document.querySelector(sel);
  const LS_KEY = "dbm_qdrant_url";

  function setStatus(msg, kind) {
    const node = $("#dbm-status");
    if (!node) return;
    node.textContent = msg || "";
    node.classList.remove("err", "ok");
    if (kind === "err") node.classList.add("err");
    if (kind === "ok") node.classList.add("ok");
  }

  function qdrantUrl() {
    return ($("#dbm-qdrant-url") && $("#dbm-qdrant-url").value.trim()) || null;
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

  function openPointsPage(documentId, coll) {
    if (!documentId || !coll || coll === "—") {
      setStatus("Thiếu document_id hoặc collection.", "err");
      return;
    }
    const qu = qdrantUrl();
    const q = new URLSearchParams({ collection: coll });
    if (qu) q.set("qdrant_url", qu);
    window.location.href = `/dbmanager/points/${encodeURIComponent(documentId)}?${q}`;
  }

  function displayTitle(row) {
    const t = (row.title || "").trim();
    if (t) return t;
    return (row.title_fallback || "").trim() || "—";
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  async function refreshList() {
    setStatus("Đang tải…");
    const r = await fetch("/api/dbmanager/indexed");
    const data = await r.json();
    if (!r.ok) {
      setStatus(data.detail || "Lỗi tải danh sách", "err");
      return;
    }
    const tbody = $("#dbm-table tbody");
    tbody.innerHTML = "";
    const items = data.items || [];
    if (!items.length) {
      setStatus("Chưa có văn bản nào được đánh dấu đã import Qdrant trong registry.", "ok");
      return;
    }
    for (const it of items) {
      const tr = document.createElement("tr");
      const coll = it.qdrant_collection || "—";
      const pts = it.qdrant_points != null ? String(it.qdrant_points) : "—";
      const docId = it.document_id || "";
      tr.innerHTML = `
        <td>${escapeHtml(displayTitle(it))}</td>
        <td><code>${escapeHtml(docId)}</code></td>
        <td><code>${escapeHtml(coll)}</code></td>
        <td>${escapeHtml(pts)}</td>
        <td>${escapeHtml((it.qdrant_imported_at || "").slice(0, 19).replace("T", " "))}</td>
        <td class="dbm-actions"></td>`;
      const tdAct = tr.querySelector(".dbm-actions");
      const bDel = document.createElement("button");
      bDel.type = "button";
      bDel.className = "btn warn dbm-btn";
      bDel.textContent = "Xoá";
      bDel.addEventListener("click", () => deleteDoc(docId, coll));
      const bIds = document.createElement("button");
      bIds.type = "button";
      bIds.className = "btn secondary dbm-btn";
      bIds.textContent = "Xem chi tiết";
      bIds.addEventListener("click", () => openPointsPage(docId, coll));
      tdAct.appendChild(bDel);
      tdAct.appendChild(bIds);
      tbody.appendChild(tr);
    }
    setStatus(`Đã tải ${items.length} dòng.`, "ok");
  }

  async function deleteDoc(documentId, collection) {
    if (!documentId || !collection || collection === "—") {
      setStatus("Thiếu document_id hoặc collection.", "err");
      return;
    }
    if (
      !confirm(
        `Xoá mọi point có document_id = "${documentId}" trong collection "${collection}"?\n` +
          "Các văn bản khác không bị ảnh hưởng.",
      )
    ) {
      return;
    }
    setStatus("Đang xoá…");
    const url = qdrantUrl();
    const r = await fetch("/api/dbmanager/qdrant/delete-by-document", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_id: documentId,
        collection,
        qdrant_url: url,
      }),
    });
    const j = await r.json();
    if (!r.ok) {
      setStatus(j.detail || "Lỗi xoá", "err");
      return;
    }
    setStatus(`Đã xoá ${j.removed_points} point khỏi ${j.collection}.`, "ok");
    await refreshList();
  }

  $("#dbm-qdrant-url")?.addEventListener("change", saveStoredUrl);
  $("#dbm-qdrant-url")?.addEventListener("blur", saveStoredUrl);

  $("#dbm-refresh").addEventListener("click", () => refreshList().catch((e) => setStatus(String(e), "err")));

  loadStoredUrl();
  refreshList().catch((e) => setStatus(String(e), "err"));
})();
