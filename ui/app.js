const form = document.getElementById("summary-form");
const input = document.getElementById("url");
const button = document.getElementById("submit");
const message = document.getElementById("message");
const result = document.getElementById("result");
const summaryApiUrl = window.SUMMARY_API_URL || "/summary";

function escapeHtml(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(
      /\[([^\]]+)\]\((https:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
}

function renderMarkdown(markdown) {
  const lines = markdown.trim().split(/\r?\n/);
  const html = [];
  let paragraph = [];
  let listType = "";

  function closeParagraph() {
    if (!paragraph.length) return;
    html.push(`<p>${paragraph.map(renderInlineMarkdown).join("<br>")}</p>`);
    paragraph = [];
  }

  function closeList() {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = "";
  }

  for (const rawLine of lines) {
    const line = rawLine.trim();

    if (!line) {
      closeParagraph();
      closeList();
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      closeParagraph();
      closeList();
      const level = heading[1].length + 1;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const unorderedItem = line.match(/^[-*]\s+(.+)$/);
    if (unorderedItem) {
      closeParagraph();
      if (listType !== "ul") {
        closeList();
        html.push("<ul>");
        listType = "ul";
      }
      html.push(`<li>${renderInlineMarkdown(unorderedItem[1])}</li>`);
      continue;
    }

    const orderedItem = line.match(/^\d+\.\s+(.+)$/);
    if (orderedItem) {
      closeParagraph();
      if (listType !== "ol") {
        closeList();
        html.push("<ol>");
        listType = "ol";
      }
      html.push(`<li>${renderInlineMarkdown(orderedItem[1])}</li>`);
      continue;
    }

    closeList();
    paragraph.push(line);
  }

  closeParagraph();
  closeList();
  return html.join("");
}

function validateHttpsUrl(value) {
  const trimmed = value.trim();
  if (!trimmed) return "Vui lòng nhập đường link HTTPS.";
  if (/\s/.test(trimmed)) return "Đường link không được chứa khoảng trắng.";

  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "https:") return "Chỉ hỗ trợ đường link bắt đầu bằng https://.";
    if (!parsed.hostname) return "Đường link không hợp lệ.";
  } catch {
    return "Đường link không hợp lệ.";
  }

  return "";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.textContent = "";
  result.textContent = "";
  result.classList.remove("visible");

  const error = validateHttpsUrl(input.value);
  if (error) {
    message.textContent = error;
    input.focus();
    return;
  }

  button.disabled = true;
  button.textContent = "Đang tóm tắt...";

  try {
    const response = await fetch(summaryApiUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: input.value.trim() }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Không thể tóm tắt link này.");
    message.textContent = "";
    result.innerHTML = renderMarkdown(data.summary || "");
    result.classList.add("visible");
  } catch (err) {
    message.textContent = err.message;
  } finally {
    button.disabled = false;
    button.textContent = "Tóm tắt";
  }
});
