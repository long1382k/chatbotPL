(() => {
  const $ = (sel) => document.querySelector(sel);

  let selectedFileId = null;
  /** Bản sao items từ /api/registry (có has_type1_saved). */
  let registryItems = [];
  let type1Editor = null;
  let retrievalEditor = null;
  /** True sau khi bấm «Tạo tóm tắt» thành công; reset khi «Tách đoạn» lại. Quyết định collection Qdrant. */
  let retrievalPreviewUsedSummarize = false;
  /** Server đã ghi chunk_preview_temp.json (sau tóm tắt). */
  let hasChunkTemp = false;
  /** Người dùng sửa preview sau tóm tắt → lưu phải gửi JSON, không dùng from_temp. */
  let retrievalTempOutdated = false;
  let suppressRetrievalChange = false;

  const optsType1 = {
    mode: "tree",
    modes: ["tree", "code"],
    name: "type1",
    mainMenuBar: true,
    navigationBar: true,
    statusBar: true,
  };

  const optsRetrieval = {
    mode: "tree",
    modes: ["tree", "code"],
    name: "retrieval",
    mainMenuBar: true,
    navigationBar: true,
    statusBar: true,
    onChange: () => {
      if (suppressRetrievalChange) return;
      if (hasChunkTemp) retrievalTempOutdated = true;
    },
  };

  function setStatus(el, msg, kind) {
    const node = typeof el === "string" ? $(el) : el;
    if (!node) return;
    node.textContent = msg || "";
    node.classList.remove("err", "ok");
    if (kind === "err") node.classList.add("err");
    if (kind === "ok") node.classList.add("ok");
  }

  function tabSwitch(step) {
    document.querySelectorAll(".step-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.step === step);
    });
    document.querySelectorAll(".panel").forEach((p) => {
      p.classList.toggle("active", p.id === `step-${step}`);
    });
  }

  document.querySelectorAll(".step-tab").forEach((b) => {
    b.addEventListener("click", () => tabSwitch(b.dataset.step));
  });

  function initEditors() {
    const je = window.JSONEditor;
    if (!je) return;
    type1Editor = new je($("#editor-type1"), optsType1, {});
    retrievalEditor = new je($("#editor-retrieval"), optsRetrieval, {});
  }

  function updateLoadType1Button() {
    const btn = $("#btn-load-type1");
    if (!btn) return;
    const item = registryItems.find((x) => x.file_id === selectedFileId);
    const can = Boolean(selectedFileId && item && item.has_type1_saved);
    btn.disabled = !can;
  }

  async function refreshRegistry() {
    const r = await fetch("/api/registry");
    const data = await r.json();
    registryItems = data.items || [];
    updateLoadType1Button();
    const tbody = $("#registry-table tbody");
    tbody.innerHTML = "";
    for (const it of data.items || []) {
      const tr = document.createElement("tr");
      const st = [];
      if (it.parsed_at) st.push("đã parse");
      else st.push("chưa parse");
      if (it.last_error) st.push(`lỗi: ${it.last_error.slice(0, 40)}…`);
      tr.innerHTML = `
        <td><input type="radio" name="pick" value="${it.file_id}" /></td>
        <td>${escapeHtml(it.original_filename)}</td>
        <td>${escapeHtml(st.join(" · "))}</td>
        <td>${it.chunked_rel_path ? "✓" : "—"}</td>
        <td>${it.qdrant_imported_at ? "✓" : "—"}</td>`;
      tbody.appendChild(tr);
    }
    tbody.querySelectorAll('input[name="pick"]').forEach((inp) => {
      inp.addEventListener("change", () => {
        selectedFileId = inp.value;
        updateLoadType1Button();
      });
    });
    if (selectedFileId) {
      const keep = [...tbody.querySelectorAll('input[name="pick"]')].find(
        (i) => i.value === selectedFileId,
      );
      if (keep) keep.checked = true;
    }
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  async function uploadList(fileList) {
    const docFiles = [...fileList].filter((f) => {
      const n = f.name.toLowerCase();
      return n.endsWith(".doc") || n.endsWith(".docx");
    });
    if (!docFiles.length) {
      setStatus("#upload-status", "Không có tệp .doc/.docx trong lựa chọn.", "err");
      return;
    }
    const fd = new FormData();
    docFiles.forEach((f) => fd.append("files", f, f.webkitRelativePath || f.name));
    setStatus("#upload-status", "Đang tải lên…");
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) {
      setStatus("#upload-status", j.detail || r.statusText, "err");
      return;
    }
    setStatus("#upload-status", `Đã nhận ${j.files.length} tệp.`, "ok");
    await refreshRegistry();
  }

  $("#files-input").addEventListener("change", (e) => uploadList(e.target.files));
  $("#folder-input").addEventListener("change", (e) => uploadList(e.target.files));

  $("#btn-parse").addEventListener("click", async () => {
    if (!selectedFileId) {
      setStatus("#step2-status", "Chọn một tệp ở bước 1 (radio).", "err");
      return;
    }
    setStatus("#step2-status", "Đang phân tích (LibreOffice nếu là .doc)…");
    const r = await fetch(`/api/parse/${selectedFileId}`, { method: "POST" });
    const j = await r.json();
    if (!r.ok) {
      setStatus("#step2-status", j.detail || "Lỗi parse", "err");
      return;
    }
    type1Editor.set(j);
    setStatus(
      "#step2-status",
      "Đã ghi bản tạm (preview). Chỉnh sửa cây rồi bấm «Lưu JSON cấu trúc» để lưu chính thức và đánh dấu đã parse.",
      "ok",
    );
    await refreshRegistry();
  });

  $("#btn-save-type1").addEventListener("click", async () => {
    if (!selectedFileId) {
      setStatus("#step2-status", "Chọn tệp ở bước 1.", "err");
      return;
    }
    let data;
    try {
      data = type1Editor.get();
    } catch (e) {
      setStatus("#step2-status", "JSON không hợp lệ: " + e.message, "err");
      return;
    }
    const r = await fetch(`/api/type1/${selectedFileId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data }),
    });
    const j = await r.json();
    if (!r.ok) {
      setStatus("#step2-status", j.detail || "Lỗi lưu", "err");
      return;
    }
    setStatus("#step2-status", "Đã lưu: " + j.path, "ok");
    await refreshRegistry();
  });

  $("#btn-load-type1").addEventListener("click", async () => {
    if (!selectedFileId) {
      setStatus("#step2-status", "Chọn tệp ở bước 1.", "err");
      return;
    }
    const item = registryItems.find((x) => x.file_id === selectedFileId);
    if (!item || !item.has_type1_saved) {
      setStatus("#step2-status", "Chưa có file JSON cấu trúc đã lưu cho tệp này.", "err");
      return;
    }
    setStatus("#step2-status", "Đang tải JSON đã lưu…");
    const r = await fetch(`/api/type1/${selectedFileId}`);
    const j = await r.json();
    if (!r.ok) {
      setStatus("#step2-status", j.detail || "Không đọc được type1.json", "err");
      await refreshRegistry();
      return;
    }
    type1Editor.set(j);
    setStatus("#step2-status", "Đã nạp JSON cấu trúc từ file.", "ok");
  });

  $("#btn-chunk").addEventListener("click", async () => {
    let type1;
    try {
      type1 = type1Editor.get();
    } catch (e) {
      setStatus("#step3-status", "JSON cấu trúc không hợp lệ: " + e.message, "err");
      return;
    }
    const docName = (type1.document_name || "").replace(/\.(docx?|DOCX?)$/i, "");
    const r = await fetch("/api/retrieval/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type1,
        document_id: docName || null,
        file_id: selectedFileId || null,
      }),
    });
    const j = await r.json();
    if (!r.ok) {
      setStatus("#step3-status", j.detail || "Lỗi tách đoạn", "err");
      return;
    }
    retrievalPreviewUsedSummarize = false;
    hasChunkTemp = false;
    retrievalTempOutdated = false;
    suppressRetrievalChange = true;
    retrievalEditor.set(j);
    suppressRetrievalChange = false;
    setStatus("#step3-status", `Tạo ${(j.children_chunks || []).length} chunk. Kiểm tra và Lưu.`, "ok");
  });

  $("#btn-summarize").addEventListener("click", async () => {
    if (!selectedFileId) {
      setStatus("#step3-status", "Chọn tệp ở bước 1 (radio).", "err");
      return;
    }
    let retrieval;
    try {
      retrieval = retrievalEditor.get();
    } catch (e) {
      setStatus("#step3-status", "Retrieval JSON không hợp lệ: " + e.message, "err");
      return;
    }
    const n = (retrieval.children_chunks || []).length;
    if (!n) {
      setStatus("#step3-status", "Chưa có chunk — hãy «Tách đoạn» trước.", "err");
      return;
    }
    setStatus("#step3-status", `Đang tạo tóm tắt cho ${n} chunk (Ollama)…`);
    const r = await fetch("/api/retrieval/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_id: selectedFileId, retrieval }),
    });
    const j = await r.json();
    if (!r.ok) {
      setStatus("#step3-status", j.detail || "Lỗi tóm tắt", "err");
      return;
    }
    retrievalPreviewUsedSummarize = true;
    hasChunkTemp = true;
    retrievalTempOutdated = false;
    suppressRetrievalChange = true;
    retrievalEditor.set(j);
    suppressRetrievalChange = false;
    setStatus(
      "#step3-status",
      "Đã ghi file tạm web_indexer/workdir/<id>/chunk_preview_temp.json và cập nhật preview. «Lưu vào data/chunked» để ghi chính thức.",
      "ok",
    );
  });

  $("#btn-save-chunked").addEventListener("click", async () => {
    if (!selectedFileId) {
      setStatus("#step3-status", "Chọn tệp ở bước 1.", "err");
      return;
    }
    const useTemp = hasChunkTemp && !retrievalTempOutdated;
    let payload;
    if (useTemp) {
      payload = { file_id: selectedFileId, from_temp: true };
    } else {
      let retrieval;
      try {
        retrieval = retrievalEditor.get();
      } catch (e) {
        setStatus("#step3-status", "Retrieval JSON không hợp lệ: " + e.message, "err");
        return;
      }
      payload = { file_id: selectedFileId, from_temp: false, retrieval };
    }
    const r = await fetch("/api/chunked/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const j = await r.json();
    if (!r.ok) {
      setStatus("#step3-status", j.detail || "Lỗi lưu chunked", "err");
      return;
    }
    hasChunkTemp = false;
    retrievalTempOutdated = false;
    setStatus("#step3-status", "Đã lưu chính thức: " + j.path, "ok");
    await refreshRegistry();
  });

  $("#btn-qdrant").addEventListener("click", async () => {
    if (!selectedFileId) {
      setStatus("#step3-status", "Chọn tệp ở bước 1.", "err");
      return;
    }
    const url = $("#qdrant-url").value.trim() || null;
    const use_dual = retrievalPreviewUsedSummarize;
    setStatus("#step3-status", "Đang mã hoá và upsert Qdrant…");
    const r = await fetch("/api/qdrant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_id: selectedFileId,
        qdrant_url: url,
        use_dual,
      }),
    });
    const j = await r.json();
    if (!r.ok) {
      setStatus("#step3-status", j.detail || "Lỗi Qdrant", "err");
      return;
    }
    setStatus(
      "#step3-status",
      `Đã upsert ${j.points} điểm → ${j.collection} (${j.vectors_mode}).`,
      "ok",
    );
    await refreshRegistry();
  });

  initEditors();
  refreshRegistry().catch((e) => setStatus("#upload-status", String(e), "err"));
})();
