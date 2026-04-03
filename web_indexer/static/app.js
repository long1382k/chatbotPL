(() => {
  const $ = (sel) => document.querySelector(sel);

  let selectedFileId = null;
  let type1Editor = null;
  let retrievalEditor = null;

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

  async function refreshRegistry() {
    const r = await fetch("/api/registry");
    const data = await r.json();
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

  $("#btn-load-type1").addEventListener("click", async () => {
    if (!selectedFileId) {
      setStatus("#step2-status", "Chọn một tệp ở bước 1 (radio).", "err");
      return;
    }
    setStatus("#step2-status", "Đang tải type1.json…");
    const r = await fetch(`/api/type1/${selectedFileId}`);
    const j = await r.json();
    if (!r.ok) {
      setStatus("#step2-status", j.detail || "Chưa có bản lưu", "err");
      return;
    }
    type1Editor.set(j);
    setStatus("#step2-status", "Đã tải từ máy chủ.", "ok");
  });

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
    setStatus("#step2-status", "Phân tích xong. Chỉnh sửa cây bên dưới nếu cần.", "ok");
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
      body: JSON.stringify({ type1, document_id: docName || null }),
    });
    const j = await r.json();
    if (!r.ok) {
      setStatus("#step3-status", j.detail || "Lỗi tách đoạn", "err");
      return;
    }
    retrievalEditor.set(j);
    setStatus("#step3-status", `Tạo ${(j.children_chunks || []).length} chunk. Kiểm tra và Lưu.`, "ok");
  });

  $("#btn-save-chunked").addEventListener("click", async () => {
    if (!selectedFileId) {
      setStatus("#step3-status", "Chọn tệp ở bước 1.", "err");
      return;
    }
    let retrieval;
    try {
      retrieval = retrievalEditor.get();
    } catch (e) {
      setStatus("#step3-status", "Retrieval JSON không hợp lệ: " + e.message, "err");
      return;
    }
    const r = await fetch("/api/chunked/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ retrieval, file_id: selectedFileId }),
    });
    const j = await r.json();
    if (!r.ok) {
      setStatus("#step3-status", j.detail || "Lỗi lưu chunked", "err");
      return;
    }
    setStatus("#step3-status", "Đã lưu: " + j.path, "ok");
    await refreshRegistry();
  });

  $("#btn-qdrant").addEventListener("click", async () => {
    if (!selectedFileId) {
      setStatus("#step3-status", "Chọn tệp ở bước 1.", "err");
      return;
    }
    const url = $("#qdrant-url").value.trim() || null;
    const collection = $("#qdrant-collection").value.trim() || "legal_chunks";
    const dry_run = $("#qdrant-dry").checked;
    setStatus("#step3-status", dry_run ? "Dry-run…" : "Đang mã hoá và upsert Qdrant…");
    const r = await fetch("/api/qdrant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_id: selectedFileId,
        collection,
        qdrant_url: url,
        dry_run,
      }),
    });
    const j = await r.json();
    if (!r.ok) {
      setStatus("#step3-status", j.detail || "Lỗi Qdrant", "err");
      return;
    }
    setStatus(
      "#step3-status",
      (dry_run ? "Dry-run: " : "Đã upsert ") + `${j.points} điểm.`,
      "ok",
    );
    await refreshRegistry();
  });

  initEditors();
  refreshRegistry().catch((e) => setStatus("#upload-status", String(e), "err"));
})();
