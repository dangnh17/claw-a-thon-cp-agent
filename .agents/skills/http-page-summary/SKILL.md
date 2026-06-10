---
name: http-page-summary
description: Summarize exactly one HTTP or HTTPS page provided by the user into a concise one-page brief.
---

# HTTP Page Summary

Use this skill when the user gives one `http://` or `https://` URL and asks for a summary of that page.

## Scope

- Summarize one page per request.
- Accept only `http://` and `https://` URLs.
- Keep the final answer short enough to fit on one page.
- Reply in the user's requested language. If no language is requested, reply in the same language as the page when clear.

## Procedure

1. Identify the single URL in the user's request.
2. If there is no URL, ask the user to provide one HTTP or HTTPS URL.
3. If there are multiple URLs, ask the user which one to summarize first.
4. Fetch the page content.
5. Extract the readable body text.
6. Ignore scripts, styles, menus, ads, cookie banners, repeated navigation, and footers when possible.
7. Preserve names, dates, numbers, claims, and technical terms accurately.
8. If the page cannot be fetched or contains no readable text, explain the issue briefly and ask for another URL.

## Output

Use this format:

```markdown
# Summary

Source: <url>

## Main Points

- <important point>
- <important point>
- <important point>

## Short Summary

<one concise paragraph>
```

## Rules

- Do not invent facts that are not present on the page.
- Do not include raw HTML.
- Do not summarize unrelated background information.
- Mention uncertainty when the source text is incomplete, ambiguous, or appears machine-generated.
- If the page is paywalled or blocked, summarize only the visible content and say that access was limited.
