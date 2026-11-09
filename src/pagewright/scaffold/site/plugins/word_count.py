"""Example plugin: counts the words on the site and adds a `words` filter.

Enable it in pagewright.toml with:  plugins = ["word_count"]
"""

import re


def words(text):
    """Template filter: {{ content | words }} -> number of words."""
    return len(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", str(text))))


filters = {"words": words}


def on_site(site):
    # Runs after all content is loaded, before rendering. site.data is available to
    # templates as site.data, so {{ site.data.total_words }} works anywhere.
    site.data["total_words"] = sum(words(page.content) for page in site.pages)


def on_site_built(site, report):
    print(f"word_count: {site.data['total_words']} words across {len(site.pages)} pages")
