#!/usr/bin/env python3

"""
IMPLEMENTATION NOTES

This python script contains no dependencies other than stdlib, so it's easy to install.

We launch the underlying codex binary with the same command-line arguments, but
(1) we set RUST_LOG="codex_core=trace,codex_mcp_server=info" so that the "codex mcp"
binary prints detailed logging to stderr including the full json that gets posted
to Anthropc, (2) we intercept stderr ourselves, and interpret that logging, and
append it to the logfile.

The log filename is chosen by the timestamp of the first entry in it, plus the
first few words of the first user prompt in it (which we get by scraping stderr).
Note: stderr has no information about resuming an old conversation, so if the user
does that then it'll all just go into the previous logfile.

The log files is an html+js preamble followed by lines of JSONL in trailing comment that
never closes. This way, (1) we can append more lines of jsonl, (2) browsers will silently
accept the unclosed comment, (3) our html preamble can obtain the content of that comment
and render it.

The html rendering happens at two levels. First, the html knows how to render arbitrary
json objects with recursively expandable nodes. Html has <details> nodes specifically for this.
  ▷ label: {"a":,"b":}

  ▽ label:
       "a": true
     ▷ "b": [...3 items]
       "c": 7

Second, the html recognizes a special {"RENDER":true, label:..., value:..., content:..., open:bool}
value in that json tree; this gets to control its own rendering -- what the expanded and collapsed
line loop like, what child nodes to show when expanded. Any html in label/value is inserted as-is.

Third, the python code that captures the POST requests and responses on stderr also recognizes
certain typical json structures in OpenAI network traffic. It choses to represent them with
a more user-friendly json tree, using that special {"RENDER":true} technique for greater control.
It also recognizes when a subsequent POST merely appends items to the input[] array from the
previous POST, and in this case it only writes jsonl for the delta. This is useful because otherwise
every single POST would be 100k+.

The html rendering is done with as minimal CSS as possible; it has only the bare minimum to achieve
the intended alignment of markers and child content -- specifically, that leaf nodes like "a":true
will left-align with nested nodes "b":[...3items] even though the nested marker has a marker ▷
to its left. This is impossible to achieve with default markers, which align the marker with "a":true.
So we (1) have our own custom marker, (2) have a predictable left gutter where the marker lives,
(3) have the label of a node start at the edge of this gutter, (4) indent leaf nodes like "a":true
to align with those labels.

Escaping is subtle. First, when the html renders json, it escapes any characters it finds in there.
Second, when html renders {"RENDER":true} blocks then it declines to escape label/value (so as to
allow the python code to insert its own html markup), hence the python is responsible for escaping
content it got from stderr before putting it into label/value. Third, to be able to append the
jsonl in a trailing comment, we escape any '-->' sequences to stop them from closing the comment.
"""

from __future__ import annotations

import os
import sys
import re
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Tuple, cast, TypedDict
try:
    from typing import NotRequired # type: ignore
except ImportError:
    from typing_extensions import NotRequired # type: ignore


preamble = """\
<html>
<head>
    <style>
        details { position: relative; padding-left: 1.25em; }
        summary { list-style: none; }
        summary::-webkit-details-marker { display: none; }
        summary::before { content: '▷'; position: absolute; left: 0; }
        details[open]>summary::before { content: '▽'; }
        details>div { margin-left: 1.25em; }
        details[open]>summary output { display: none; }
    </style>
    <script>
        function fromHTML(html) {
            const t = document.createElement('template');
            t.innerHTML = html.trim();
            return t.content.firstElementChild;
        }

        function esc(s) {
            return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\\\\n/g, "<br/>");
        }

        function buildNode(value, label) {
            if (value && typeof value === 'object' && value?.RENDER && value?.content === undefined) {
                return fromHTML(`<div>${value?.label ?? ''}${value?.value ?? ''}</div>`);
            } else if (value && typeof value === 'object') {
                const title = value?.RENDER ? (value.label ?? '') : esc(label);
                const inline = value?.RENDER ? (value?.value ?? '') : esc(
                    Array.isArray(value)
                        ? `[...${value.length} items]`
                        : '{' + Object.keys(value).map(k => `${JSON.stringify(k)}:`).join(',') + '}'
                );
                const d = fromHTML(`<details><summary>${title}<output>${inline}</output></summary></details>`);
                d.addEventListener('toggle', () => {
                    const content = value?.RENDER ? value.content : value;
                    if (Array.isArray(content)) {
                        content.forEach((item, i) => d.appendChild(buildNode(item, `${i + 1}: `)));
                    } else if (content && typeof content === 'object') {
                        Object.keys(content).forEach(k => d.appendChild(buildNode(content[k], `${JSON.stringify(k)}: `)));
                    } else {
                        d.appendChild(buildNode(content, ''));
                    }
                }, { once: true });
                d.open = value?.RENDER ? (value?.open ?? false) : false;
                return d;
            } else {
                return fromHTML(`<div>${esc(label)}${esc(JSON.stringify(value))}</div>`);
            }
        }

        window.addEventListener('DOMContentLoaded', () => {
            const output = document.createElement('div');
            document.body.appendChild(output);

            if (document.lastChild && document.lastChild.nodeType === Node.COMMENT_NODE && document.lastChild.data.trim()) {
                for (const line of document.lastChild.data.split(/\\r?\\n/).filter(Boolean)) {
                    output.appendChild(buildNode(JSON.parse(line), 'json:'));
                    output.appendChild(document.createElement('hr'));
                }
            }
        });
    </script>
</head>
<body>
</body>
</html>
<!--
"""

def unescape_rust(s: str) -> str:
    return s.replace('\\\\', '\x00').replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t').replace('\\"', '"').replace('\x00', '\\')


def esc(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def calculate_json_delta(prev: Any, new: Any) -> Tuple[bool, Any]:
    """Given two json values, returns a bool for whether they're identical,
    plus a json representation of the difference, intended for humans to read.
    The json representation always has the same type as 'new'.
    """
    if isinstance(prev, dict) and isinstance(new, dict):
        prevl, newl = cast(dict[str,Any], prev), cast(dict[str,Any], new)
        prev_keys = set(prevl.keys())
        new_keys = set(newl.keys())
        delta: dict[str, Any] = {}

        for k in sorted(prev_keys - new_keys):
            delta[f"-{k}"] = None
        for k in sorted(new_keys - prev_keys):
            delta[f"+{k}"] = newl[k]
        for k in sorted(prev_keys & new_keys):
            identical, subdelta = calculate_json_delta(prevl[k], newl[k])
            if not identical:
                if isinstance(subdelta, list) and subdelta[0] == "...":
                    delta[f"{k}+"] = subdelta[1:]
                else:
                    delta[f"*{k}"] = subdelta
        return (len(delta) == 0, delta)
    elif isinstance(prev, list) and isinstance(new, list):
        prevl, newl = cast(list[Any], prev), cast(list[Any], new)
        if len(prevl) <= len(newl):
            all_true = all(calculate_json_delta(a,b)[0] for (a,b) in zip(prevl, newl))
            if all_true and len(prevl) == len(newl):
                return (True, [])
            elif all_true and len(prevl) < len(newl):
                return (False, ["...", *newl[len(prevl):]])
        return (False, newl)
    else:
        return (prev == new, new)


class Render(TypedDict):
    """We are in the business of producing json that will be rendered by log.html.
    Normally it has a simple json rendering: everything has a label, primitives are
    shown as-is, and arrays and objects are shown as collapsible <detail> nodes,
    with an inline-value that gets shown only when collapsed, and labelled children
    shown only when expanded.
    - Collapsed object:
      ▷ label: {"a":,"b":}
    - Expanded object:
      ▽ label:
          "a": true
          "b": 7
    - Collapsed array:
      ▷ label: [...2 items]
    - Expanded array:
      ▽ label:
          1: "hello"
          2: 7

    But if a value is a Render object (i.e. has mandatory field RENDER=true),
    then it gets to control its own rendering:
    1. It controls its own label
    2. It controls whether it should be initially open or closed (default closed)
    3. It controls what inline summary to show when collapsed (default nothing)
    4. It controls what children to show when expanded (default absent).
       If absent, the value won't be collapsible and will always display label+value;
       if a primitive, the value will be collapsible and will show one unlabelled child;
       if array/dictionary, all children will be shown with appropriate labels.

    Note that label and value are inserted as html straight into the DOM. This means
    they can include html markup, but you'll need to escape anything that came from elsewhere.
    """
    RENDER: Literal[True]  # must be True
    label: NotRequired[str]  # overrides default label (default no label); includes colon and space
    open: NotRequired[bool]  # whether the content is initially expanded (default False)
    value: NotRequired[str]  # what to display after the label
    content: NotRequired[Any]  # the expanded form will render children of content array/obj, or content itself if primitive


def short(s: str) -> str:
    lines = s.splitlines()
    return " ".join(s.split()[:15])[:80] + f"... [{len(lines)} lines]"

def render_function_call(i: dict[str, Any], bold: bool) -> Render:
    try:
        arguments = json.loads(i["arguments"])
    except (json.JSONDecodeError, TypeError):
        arguments = i["arguments"]
    shortargs = "..."
    if i['name'] == "shell" and isinstance(arguments, dict) and "command" in arguments and isinstance(arguments["command"], list):
        command = cast(list[str], arguments["command"])
        if len(command) >=3 and command[0] == "bash" and command[1].startswith("-"):
            shortargs = esc(command[2][:50].replace("\n", " ") + ("..." if len(command[2])> 50 else ""))
    b1,b2 = ("<b>", "</b>") if bold else ("", "")
    return {"RENDER": True, "label": f"function_call: {b1}{esc(i['name'])}({shortargs}){b2}", "content": arguments}


def render_delta(delta: Any) -> Any:
    """Given a delta as produced by calculate_json_delta, this normally
    just returns delta as-is. But if we recognize certain familiar forms
    typical of OpenAI json requests, then we'll pretty-print them
    using Render objects.
    We trust that the input json will only ever be the json that's sent to OpenAI,
    or a delta based on it. Therefore not much error checking is needed."""    
    if not isinstance(delta, dict):
        return delta
    d = cast(dict[str,Any], delta)
    if {"instructions","tools","input","input+"}.isdisjoint(d):
        return d
    try:
        content: list[Render] = []
        if "instructions" in d:
            content.append({"RENDER": True, "label": "instructions: ", "value":esc(short(d["instructions"])), "content": d["instructions"]})
        if "tools" in d:
            tools_content: list[Render] = []
            for tool in d["tools"]:
                tool = cast(dict[str,Any], tool)
                tools_content.append({"RENDER": True, "label": f"tool: {esc(tool['name'])}", "content": tool["parameters"] | {"description": tool["description"]}})
            content.append({"RENDER": True, "label": "tools: ", "value": f"[{len(d['tools'])} tools]", "content": tools_content})
        if "input" in d or "input+" in d:
            input_blocks: list[dict[str, Any]] = d.get("input", d.get("input+", []))
            for i in input_blocks:
                if i["type"] == "message":
                    for c in cast(list[dict[str,Any]], i["content"]):
                        value = short(re.split(r'## My request for Codex:\s*', c["text"], maxsplit=1)[-1])
                        b1,b2 = ("<b>", "</b>") if c["type"] == "input_text" else ("", "")
                        content.append({"RENDER": True, "label": f"{esc(c['type'])}: ", "value": f"{b1}{esc(value)}{b2}", "content": c["text"]})
                elif i["type"] == "function_call":
                    content.append(render_function_call(i, bold=False))
                elif i["type"] == "function_call_output":                    
                    try:
                        output = json.loads(i["output"])                        
                    except json.JSONDecodeError:
                        output = i["output"]
                    if isinstance(output, dict) and "output" in output and isinstance(output["output"], str):
                        output = output["output"]
                        value = short(output)
                    else:
                        value = ""
                    content.append({"RENDER": True, "label": "function_call_output: ", "value": f"<b>{esc(value)}</b>", "content": output})
        content.append({"RENDER": True, "label": "[raw json]", "content": delta})
        return {"RENDER": True, "label": f"[{datetime.now().strftime('%H:%M:%S')}] POST", "open": True, "content": content}
    except Exception:
        return d

def render_response(response: dict[str, Any]) -> Any:
    try:
        content: list[Render] = []
        for output in response["response"]["output"]:
            if output["type"] == "reasoning":
                reasoning: list[Render] = []
                for summary in output["summary"]:
                    reasoning.append({"RENDER": True, "label": f"{esc(summary['type'])}: ", "value": f"{esc(short(summary['text']))}", "content": summary["text"]})
                content.append({"RENDER": True, "label": f"reasoning: [{len(str(output.get('encrypted_content', '')))} bytes]", "open": True, "content": reasoning})
            elif output["type"] == "function_call":
                content.append(render_function_call(output, bold=True))
            elif output["type"] == "message":
                for c in output["content"]:
                    text = str(c.get('text', '???'))
                    content.append({"RENDER": True, "label": f"{esc(c['type'])}: ","value": f"<b>{esc(short(text))}</b>", "content": text})
        content.append({"RENDER": True, "label": "[raw json]", "content": response})
        return {"RENDER": True, "label": f"[{datetime.now().strftime('%H:%M:%S')}] response.completed", "open": True, "content": content}
    except Exception:
        return response

def main() -> int:
    # We can't use shutil.which("codex") to find the underlying codex binary. That will
    # pick up whichever codex is installed in the user's PATH, e.g. /opt/homebrew/bin/codex.
    # But the VSCode codex extension is expecting to launch the codex binary that came bundled
    # with the extension, and we must preserve this behavior (otherwise there'll be version
    # mismatches). Our only clue to finding the correct codex binary is that the extension
    # also sticks its location at the end of PATH. We have no choice but to trust this behavior
    # (it's our only hope to find codex) and so it makes no sense to be defensive here.
    proc = subprocess.Popen(
        [str(Path(os.environ.get("PATH", "").split(os.pathsep)[-1]) / "codex"), *sys.argv[1:]],
        env=os.environ.copy() | {"RUST_LOG": "codex_core=trace,codex_mcp_server=info"},
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # line-buffered
    )

    assert proc.stderr is not None
    timestamp = datetime.now()
    prompt: str | None = None
    prev: Any = None
    poisoned: bool = False

    def log(jj: Any) -> None:
        nonlocal prompt, timestamp
        prompt = prompt or ""
        log = Path(os.path.expanduser("~/codex-trace")) / f"{timestamp.strftime('%Y-%m-%dT%H%M%S')}{prompt}.html"
        log.parent.mkdir(parents=True, exist_ok=True)
        need_preamble = not log.exists()
        with log.open("a", encoding="utf-8") as f:
            if need_preamble:
                f.write(preamble)
            f.write(json.dumps(jj, ensure_ascii=False).replace(">", "\\u003e").replace("--", "-\\u002d") + "\n")

    for line in proc.stderr:
        try:
            if poisoned:
                pass
            elif re.search(r"^[^{]*Configuring session", line):
                timestamp = datetime.now()
                prompt = None
                prev = None                
            elif re.search(r"^[^{]*Submission", line):
                m = re.search(r'Text\s*\{\s*text:\s*"((?:[^"\\]|\\.)*)"', line)
                text = unescape_rust(m.group(1) if m else "")
                text = re.split(r'## My request for Codex:\s*', text, maxsplit=1)[-1]
                text = re.sub(r'[^\w \-]+', '', text, flags=re.ASCII)
                text = " ".join(text.strip().split(" ")[:5])[:20].strip()
                prompt = prompt if prompt is not None else " - " + text if text else ""
            elif re.search(r"^[^{]*POST to[^{]*{", line):
                try:
                    post = json.loads(line[line.find("{"):].strip())
                except json.JSONDecodeError:
                    post = {"ERROR": "Malformed json"}
                _, delta = calculate_json_delta(prev, post)
                log(render_delta(delta))
                prev = post
            elif re.search(r"^[^{]*SSE event: [^{]*{", line):
                try:
                    response = json.loads(line[line.find("{"):].strip())
                except json.JSONDecodeError:
                    continue
                if response.get("type","") != "response.completed":
                    continue
                log(render_response(response))
        except Exception as e:
            print(f"Error processing line: {e}", file=sys.stderr)
            # The above error message will go to the "Codex" output pane in VSCode.
            # We must continue to drain proc's stderr, else it will block...
            poisoned = True

    return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
