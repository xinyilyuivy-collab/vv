const state = {
  currentTime: "",
  period: "",
  fundCode: "",
  fundName: "",
  theme: "",
  postType: "",
  marketNotes: "",
  accessToken: localStorage.getItem("fresh_app_access_token") || "",
};

const fundMap = {
  "017560": "科创芯片",
  "011145": "汇宏",
  "025759": "新兴动力",
  "020982": "机器人",
  "160424": "创业板50",
  "014542": "新能源主题",
  "025733": "航天航空",
  "020867": "港股央企红利",
  "007168": "安和债券",
  "016071": "智联混合",
  "000217": "黄金",
  "017825": "新材料",
};

function bindSingleSelect(selector, key, attrName) {
  document.querySelectorAll(selector).forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(selector).forEach((node) => node.classList.remove("active"));
      button.classList.add("active");
      state[key] = button.dataset[attrName];
      if (key === "fundCode") {
        state.fundName = fundMap[state.fundCode] || "";
      }
      updatePreview();
      updateButtonState();
    });
  });
}

function authHeaders() {
  const headers = {};
  if (state.accessToken) {
    headers["X-Access-Token"] = state.accessToken;
  }
  return headers;
}

function ensureAccessToken() {
  if (!state.accessToken) {
    const input = window.prompt("请输入访问口令");
    if (input) {
      state.accessToken = input.trim();
      localStorage.setItem("fresh_app_access_token", state.accessToken);
    }
  }
}

function resetAccessToken() {
  state.accessToken = "";
  localStorage.removeItem("fresh_app_access_token");
}

async function loadContext() {
  ensureAccessToken();
  const response = await fetch("/api/context", {
    headers: authHeaders(),
  });
  const data = await response.json();
  if (response.status === 401) {
    resetAccessToken();
    throw new Error("访问口令已失效，请重新输入");
  }
  if (data.error) throw new Error(data.error);

  state.currentTime = data.current_time || "";
  state.period = data.period || "";
  state.marketNotes = data.market_notes || "";
  document.getElementById("timeDisplay").textContent = state.currentTime || "未读取";
  document.getElementById("periodDisplay").textContent = state.period || "未读取";
  document.getElementById("modelDisplay").textContent = data.model_name || data.model_provider || "未读取";
  document.getElementById("heroModelDisplay").textContent = data.model_name || data.model_provider || "未读取";
  document.getElementById("marketNotes").textContent = state.marketNotes || "暂无行情记录";
}

function updatePreview() {
  document.getElementById("previewFund").textContent = state.fundName ? `${state.fundName}（${state.fundCode}）` : "未选择";
  document.getElementById("previewTheme").textContent = state.theme || "未选择";
  document.getElementById("previewType").textContent = state.postType || "未选择";
  document.getElementById("previewCount").textContent = document.getElementById("countInput").value || "5";
  document.getElementById("hotspotPreview").textContent = document.getElementById("hotspotInput").value.trim() || "尚未填写";
}

function updateButtonState() {
  const ready = state.currentTime && state.fundCode && state.theme && state.postType && document.getElementById("hotspotInput").value.trim();
  const button = document.getElementById("generateBtn");
  button.disabled = !ready;
  document.getElementById("statusText").textContent = ready ? "可以开始生成" : "等待配置完成";
}

function splitPosts(content) {
  return content.split(/^---$/m).map((item) => item.trim()).filter(Boolean);
}

function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderPosts(content) {
  const posts = splitPosts(content);
  const list = document.getElementById("resultList");
  if (!posts.length) {
    list.innerHTML = '<div class="empty-state">模型没有返回内容。</div>';
    return;
  }

  list.innerHTML = posts.map((post, index) => {
    const lines = post.split("\n").filter(Boolean);
    const hasTitle = state.postType !== "短帖";
    const title = hasTitle ? lines[0] : "";
    const body = hasTitle ? lines.slice(1).join("\n") : post;
    return `
      <article class="result-item">
        <div class="result-head">
          <strong>#${index + 1} · ${escapeHtml(state.postType)}</strong>
          <button class="secondary-btn" type="button" data-copy="${escapeHtml(post)}">复制本条</button>
        </div>
        <div class="result-body" data-raw="${escapeHtml(post)}">
          ${hasTitle ? `<span class="result-title">${escapeHtml(title)}</span>` : ""}
          ${escapeHtml(body)}
        </div>
      </article>
    `;
  }).join("");

  list.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(decodeHtml(button.dataset.copy));
      const old = button.textContent;
      button.textContent = "已复制";
      setTimeout(() => {
        button.textContent = old;
      }, 1600);
    });
  });
}

function decodeHtml(text) {
  const textarea = document.createElement("textarea");
  textarea.innerHTML = text;
  return textarea.value;
}

async function generate() {
  const status = document.getElementById("statusText");
  const list = document.getElementById("resultList");
  status.textContent = "生成中...";
  list.innerHTML = '<div class="empty-state">正在调用本地模型，请稍候...</div>';

  try {
    const payload = {
      fund_code: state.fundCode,
      fund_name: state.fundName,
      theme: state.theme,
      post_type: state.postType,
      count: parseInt(document.getElementById("countInput").value || "5", 10),
      current_time: state.currentTime,
      period: state.period,
      hotspot: document.getElementById("hotspotInput").value.trim(),
      extra: document.getElementById("extraInput").value.trim(),
    };

    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (response.status === 401) {
      resetAccessToken();
      throw new Error("访问口令已失效，请刷新页面后重新输入");
    }
    if (data.error) throw new Error(data.error);
    renderPosts(data.content || "");
    status.textContent = "生成完成";
  } catch (error) {
    list.innerHTML = `<div class="empty-state">生成失败：${escapeHtml(error.message)}</div>`;
    status.textContent = "生成失败";
  }
}

async function copyAll() {
  const blocks = Array.from(document.querySelectorAll(".result-body")).map((node) => decodeHtml(node.dataset.raw || "")).filter(Boolean);
  if (!blocks.length) {
    return;
  }
  await navigator.clipboard.writeText(blocks.join("\n\n---\n\n"));
  const button = document.getElementById("copyAllBtn");
  const old = button.textContent;
  button.textContent = "已复制";
  setTimeout(() => {
    button.textContent = old;
  }, 1600);
}

document.getElementById("countInput").addEventListener("input", () => {
  updatePreview();
  updateButtonState();
});
document.getElementById("hotspotInput").addEventListener("input", () => {
  updatePreview();
  updateButtonState();
});
document.getElementById("extraInput").addEventListener("input", updatePreview);
document.getElementById("generateBtn").addEventListener("click", generate);
document.getElementById("copyAllBtn").addEventListener("click", copyAll);
document.getElementById("resetTokenBtn").addEventListener("click", () => {
  resetAccessToken();
  window.alert("访问口令已清除，请刷新页面后重新输入。");
});
document.getElementById("refreshContextBtn").addEventListener("click", async () => {
  try {
    await loadContext();
    updateButtonState();
  } catch (error) {
    document.getElementById("marketNotes").textContent = `上下文读取失败：${error.message}`;
  }
});

document.getElementById("navRefreshBtn").addEventListener("click", async () => {
  const btn = document.getElementById("navRefreshBtn");
  const status = document.getElementById("navRefreshStatus");
  btn.disabled = true;
  status.textContent = "正在刷新...";
  try {
    const response = await fetch("/api/nav-refresh", {
      headers: authHeaders(),
    });
    const data = await response.json();
    if (response.status === 401) {
      resetAccessToken();
      throw new Error("访问口令已失效，请刷新页面后重新输入");
    }
    if (data.error) throw new Error(data.error);
    await loadContext();
    status.textContent = `✓ ${data.message || "净值已刷新"}`;
    setTimeout(() => { status.textContent = ""; }, 3000);
  } catch (error) {
    status.textContent = `✗ 刷新失败：${error.message}`;
    setTimeout(() => { status.textContent = ""; }, 5000);
  } finally {
    btn.disabled = false;
  }
});

bindSingleSelect("#fundOptions .option-card", "fundCode", "fund");
bindSingleSelect("#themeOptions .chip", "theme", "theme");
bindSingleSelect("#typeOptions .chip", "postType", "type");

loadContext()
  .then(() => {
    updatePreview();
    updateButtonState();
  })
  .catch((error) => {
    document.getElementById("timeDisplay").textContent = "读取失败";
    document.getElementById("periodDisplay").textContent = "读取失败";
    document.getElementById("heroModelDisplay").textContent = "读取失败";
    document.getElementById("marketNotes").textContent = `上下文读取失败：${error.message}`;
  });
