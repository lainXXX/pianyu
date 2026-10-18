# Create output styles with AI

[简体中文](style-guide.md) · English

Give this page to an AI and describe how you want the content organized and presented. Ask it for a JSON style package, then select the file under **Settings → Output styles → Import**. Its prompt and layouts are saved as independent new items without overwriting existing ones.

[Download an importable example](style-package.example.json) · [Project](https://github.com/lainXXX/pianyu)

## Choose a content type

A style determines **what to write and how to organize it**. A layout determines **how that text becomes an image**. A style can bind several compatible layouts; the first is its default. The reader only offers layouts bound to the selected style.

The global `system_prompt` handles shared transcription corrections, factual grounding, and expression rules. A style prompt should specify only its own content organization and voice. Do not repeat the global rules or ask the model to ignore them.

| `kind` | Suitable content | Prompt variables | Required model response | Layout `group` |
| --- | --- | --- | --- | --- |
| `free` | Notes, articles, Q&A, action lists | `{title}`, `{text}` | Markdown body | `article` |
| `points` | Structured points for comparisons, news, concept cards | `{title}`, `{text}`, `{max_points}` | Points JSON described below | `points` |
| `source` | Paragraph-by-paragraph transcript correction | `{title}`, `{paragraphs}` | JSON array of paragraphs | `article` |
| `structure` | Organizing original sentences into sections and highlighting key passages | `{title}`, `{sentences}` | JSON containing sentence references | `article` |

Start with **`free` + `article`**. Use `points` when image summaries and the full reading text need separate treatment. Transcript-based types must keep their prescribed response formats.

## Instructions to give an AI

Send the following instructions together with your requirements and the [example JSON](style-package.example.json):

```text
Create an importable JSON style package for Pianyu.

My requirements: <audience, content organization, suitable videos, visual preferences>

Return only JSON, without code fences or explanations.
Keep every field and type from the example, use version=1, and add no fields.
Use free + article by default; use points + points for structured points.
Include one style and all its layouts. Reference package-local IDs in style.layouts,
ordered with the default layout first.

Keep the prompt brief and specific. Use Markdown headings and, where useful,
XML tags to separate the title and transcript.
Keep {title} and {text} as variables; do not replace them with sample content.
Do not include API keys or service configuration.
Specify content structure and style without repeating or overriding global correction rules.
The generated content must follow the data contract for the selected kind.

Use self-contained HTML, CSS, and declarative Jinja for/if statements only.
Include no scripts, external resources, or network requests.
An article layout must have a class="content" container, iterate over blocks,
and output block.html. Do not hardcode the body text.
Keep footer_lines to display source attribution and the Pianyu link.
Use first/last/page/pages to control titles and footers.
Allow long text to paginate naturally. Do not give the content container a fixed height,
overflow:hidden, or line clamping.
Use fonts already installed on the device, without remote fonts.
Leave page margins and keep both body text and footers readable.

Check JSON escaping, prompt variables, Jinja variables, group compatibility,
and all layout references before returning the package.
```

Save the result as a UTF-8 `.json` file. Within JSON strings, encode newlines as `\n` and double quotes as `\"`. Use a JSON serializer rather than assembling long HTML strings by hand.

## Package format

The [complete example](style-package.example.json) contains all required fields. Its structure is:

```text
{
  format: "pianyu-style",
  version: 1,
  style: {name, kind, prompt, layouts: [package-local layout IDs]},
  layouts: [{id, name, group, html, width, height_mode}]
}
```

This outline is not importable JSON; use the linked example as the starting point. Do not add model or credential configuration. Package-local layout IDs are only references: Pianyu assigns independent IDs on import.

- Each package contains one style and **1–10 layouts**, with a maximum file size of **1 MiB**.
- A prompt may contain at most 20,000 characters; each layout's HTML may contain at most 100,000 characters.
- `width` must be an integer from 320 to 4000. `height_mode` must be `auto` or `fixed:300` through `fixed:8000`. Use `fixed:1600` as a starting point for articles and `auto` for long points images.
- `style.layouts` must reference every layout in the package exactly once. Its first entry is the default layout.
- Layout IDs must contain 1–80 ASCII letters, digits, underscores, or hyphens.
- Every field is required. Unknown fields, incompatible groups, scripts, event handlers, external images, external CSS, and remote fonts are unsupported.
- Keep Jinja to simple variables, `for`, `if`, and basic formatting filters. Avoid function calls, macros, assignments, private attributes, exponentiation, and string expansion. Two nested loops are allowed for “each point → its details or quotes”; do not cross-iterate over the entire dataset.

## Prompt input and response contracts

Single braces in a prompt identify Pianyu variables, such as `{text}`. Double braces in layouts identify Jinja variables, such as `{{ title }}`; loops use `{% for ... %}`. Keep these syntaxes separate. JSON examples inside prompts do not need all their braces doubled.

### `free`

The inputs are transcript text `{text}` and the video title `{title}`. Return Markdown, including headings, paragraphs, lists, quotations, and tables as needed. The reader renders Markdown rather than displaying its syntax. Image rendering supports headings, paragraphs, lists, quotations, and inline emphasis; complex tables or arbitrary HTML may differ from the reader.

The title provides grounding; it does not permit adding facts the video never mentions. Specify which relevant examples, data, and conditions to preserve. Avoid vague demands such as “be exhaustive” or “sound highly professional.”

### `points`

Return a JSON object without code fences:

```json
{
  "title": "Topic",
  "conclusion": "Main conclusion",
  "points": [{
    "heading": "One point",
    "body": "A short summary for the image",
    "text": "A complete reading explanation, retaining data, conditions, and reasoning.",
    "key": true,
    "detail": ["Necessary additional information not repeated in the main text"],
    "quotes": [{"quote": "Edited quotation", "source": "Verbatim source sentence"}]
  }]
}
```

Return at most `{max_points}` points, currently 10. At most three may have `key=true`, with at most four quotations in total. Use empty arrays for `detail` and `quotes` when there is nothing necessary to add or no reliable quotation. `source` must be a verbatim sentence from the transcript, never a rewrite by the AI. Quotations are checked against their sources.

### `source`

`{paragraphs}` is a JSON array of objects containing `n` and `text`. Return an array with the same IDs and number of items:

```json
[{"n": 1, "text": "Corrected paragraph", "removed": ["Complete original sentence that was removed"]}]
```

Only correct transcription, remove fillers, and add paragraph breaks. Preserve numbers, negation, and relationships. When removing a complete sentence unrelated to the topic, record its original wording in `removed`. The application checks edits and restores rejected changes. Do not use this type to generate summaries.

### `structure`

`{sentences}` is an array of numbered sentences containing `i` and `s`. Return their organization:

```json
{
  "sections": [{"heading": "Section heading", "level": 2, "blocks": [
    {"type": "p", "ids": [1, 2]},
    {"type": "list", "items": [[3], [4, 5]]}
  ]}],
  "removed": [6],
  "fixes": {"2": "Complete corrected sentence"},
  "key": [1]
}
```

Each input ID must appear exactly once in either the body or `removed`. `fixes` and `key` may only reference input IDs. Pianyu rebuilds the body from the original sentences; do not return newly written paragraphs or invent IDs.

## Layout variables

| `article` | Meaning |
| --- | --- |
| `title`, `kicker` | Title and short source description |
| `blocks` | Body blocks; `block.html` is prepared and escaped by the application, ready to output |
| `footer_lines` | Source attribution, notices, and project link; output each line |
| `page`, `pages` | Current page and total page count |
| `first`, `last` | Whether this is the first or last page |

Place the body inside a `.content` container. Do not escape `block.html` again or hardcode `height:1600px`, which can clip text. Pagination uses measurements from the actual browser rendering.

| `points` | Meaning |
| --- | --- |
| `title`, `conclusion`, `kicker` | Title, conclusion, and short source description |
| `points` | Points array with the fields shown in the JSON above |
| `left`, `right` | Balanced subsets of the points for two-column layouts |
| `quotes` | Quotations from all points |
| `footer_lines` | Image footer lines |

For example: `{% for p in points %}<h2>{{ p.heading }}</h2><p>{{ p.body }}</p>{% endfor %}`. Jinja automatically escapes these fields. Do not assemble source text into executable HTML.

## Check an imported style

1. Select Preview in the layout list. Check long titles, body text, footers, and reading at narrow widths.
2. Generate from a short video, then a longer one. Check data, quotations, and which content was retained or omitted.
3. Switch between bound layouts on the result page. Check clipping and page numbers. Switching layouts does not call the model again.
4. Download images, copy text, and export Markdown. Inspect the resulting files.
5. When satisfied, share the JSON using the export icon beside the style. Packages do not include API keys, cookies, global rules, or generation history.

A failed import leaves existing styles and layouts intact. If the AI generates an invalid package, give it the original error and these rules to correct the result. Do not remove necessary content or attribution just to make the import succeed.
