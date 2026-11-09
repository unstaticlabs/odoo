# USL user guide

This guide ships with the release you are using, so it always describes the
product in front of you. Open it from **Help** in the user menu: the guide lands
on the page about the screen you were on.

Pages come in four kinds, and the pill at the top of each one says which:

- **Tutorial**: learn the product by doing, from nothing.
- **How-to guides**: get one task done. Pages marked *Last tested* are generated
  from the product's own browser journeys, with the screens captured at the
  moment each step ran; the pill opens the test record.
- **Reference**: look something up. The pages under *generated* come from the
  product's code and cannot drift from it.
- **Explanation**: understand why the product behaves as it does.

The navigation groups pages by application; the search box matches titles and
page text. The active company controls the documents and values you see, so
switch company in the top bar before starting a task for another company.

Agents read the same guide: add `?format=md` to any page for its Markdown,
`?format=json` for its facts, or open `llms.txt`, `llms-full.txt` and
`index.json` beside this page. Through MCP, the `usl.docs.guide` model offers
`search_pages`, `read_page` and `list_pages`.
