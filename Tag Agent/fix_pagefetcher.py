from pathlib import Path

p = Path("martech_tag_auditor.py")
lines = p.read_text(encoding="utf-8").splitlines()

start = next(i for i, line in enumerate(lines) if line.lstrip().startswith("def _fetch_static"))
end = next(i for i, line in enumerate(lines) if line.startswith("class TagScanner"))

fixed_block = '''    def _fetch_static(self) -> bool:
        try:
            start = time.time()
            response = requests.get(
                self.url,
                headers=self.HEADERS,
                timeout=self.timeout,
                allow_redirects=True,
                verify=False
            )
            self.load_time_ms = int((time.time() - start) * 1000)
            self.response_status = response.status_code
            self.response_headers = dict(response.headers)
            self.html = response.text or ""
            self._collect_scripts_from_html()
            return True
        except RequestException as exc:
            print(f"Static fetch failed: {exc}")
            return False

    def _fetch_playwright(self) -> bool:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=self.HEADERS["User-Agent"],
                    locale="en-US",
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                page = context.new_page()

                page.on("request", lambda req: self.network_requests.append({
                    "url": req.url,
                    "method": req.method,
                    "resource_type": req.resource_type,
                    "timestamp": time.time(),
                }))
                page.on("response", lambda res: self.network_responses.append({
                    "url": res.url,
                    "status": res.status,
                    "timestamp": time.time(),
                }))
                page.on("console", lambda msg: self.console_errors.append({
                    "type": msg.type,
                    "text": msg.text,
                }) if msg.type in ("error", "warning") else None)

                start = time.time()
                try:
                    response = page.goto(self.url, wait_until="networkidle", timeout=self.timeout * 1000)
                    if response:
                        self.response_status = response.status
                        self.response_headers = dict(response.headers)
                except PlaywrightTimeout:
                    pass

                self.load_time_ms = int((time.time() - start) * 1000)
                self.page_title = page.title()
                self.html = page.content()
                self.cookies = context.cookies()

                self.inline_scripts = []
                self.external_scripts = []
                for script in page.query_selector_all("script"):
                    src = script.get_attribute("src")
                    if src:
                        self.external_scripts.append(src)
                    else:
                        try:
                            content = script.inner_text()
                            if content.strip():
                                self.inline_scripts.append(content)
                        except Exception:
                            pass

                browser.close()
                return True

        except Exception as exc:
            print(f"Playwright failed ({exc}). Falling back to static fetch.")
            return self._fetch_static()
'''

lines[start:end] = fixed_block.splitlines()
p.write_text("\n".join(lines) + "\n", encoding="utf-8")

print("Fixed _fetch_static and _fetch_playwright indentation")
