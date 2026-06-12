from __future__ import annotations


def parse_policy_markdown(markdown_text: str) -> list[dict]:
    """Parse policy markdown into structured chunks by H2 > H3 sections."""
    chunks = []
    lines = markdown_text.split("\n")

    current_h2 = None
    current_h3 = None
    current_content_lines: list[str] = []

    def flush_chunk() -> None:
        if current_h2 and current_h3:
            content = "\n".join(current_content_lines).strip()
            citation = f"{current_h2} > {current_h3}"
            rendered_text = f"## {current_h2}\n### {current_h3}\n{content}"
            chunks.append(
                {
                    "section_h2": current_h2,
                    "section_h3": current_h3,
                    "citation": citation,
                    "rendered_text": rendered_text,
                }
            )

    for line in lines:
        if line.startswith("## "):
            flush_chunk()
            current_h2 = line[3:].strip()
            current_h3 = None
            current_content_lines = []
        elif line.startswith("### "):
            flush_chunk()
            current_h3 = line[4:].strip()
            current_content_lines = []
        else:
            if current_h3 is not None:
                current_content_lines.append(line)

    flush_chunk()
    return chunks
