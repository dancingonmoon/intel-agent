import unittest

from intel.config import load_topic
from intel.contracts import Window
from intel.dedupe import canonicalize_url, group_articles
from intel.pipeline import _build_digest
from intel.relevance import classify, is_relevant
from intel.sources.base import Article, SourceResult


def article(title: str, snippet: str = "", url: str = "https://example.com/a", source: str = "Media", kind: str = "media", published: str = "2026-04-10T10:00:00+03:00", lang: str = "ru") -> Article:
    return Article(
        title=title,
        url=url,
        source=source,
        source_kind=kind,
        published_at=published,
        snippet=snippet,
        lang=lang,
    )


class RelevanceTests(unittest.TestCase):
    def setUp(self):
        self.topic = load_topic("ru-tetra")

    def test_contract_story_is_relevant_and_classified(self):
        item = article(
            "«Росэл» выиграл тендер на поставку TETRA-оборудования",
            "Россия, контракт на базовые станции для энергетиков",
        )
        self.assertTrue(is_relevant(item, self.topic))
        primary, secondary, confidence, terms = classify(item, self.topic)
        self.assertEqual(primary, "contracts")
        self.assertIn("тендер", terms)
        self.assertIn(confidence, {"high", "medium", "low"})

    def test_technology_story_is_not_players(self):
        item = article(
            "«Информтехника» выпустила первую российскую портативную TETRA-радиостанцию",
            "Россия, МиниКом-АНР-3, серийное производство",
            source="CNews",
        )
        self.assertTrue(is_relevant(item, self.topic))
        primary, _, _, _ = classify(item, self.topic)
        self.assertEqual(primary, "technology")

    def test_tetra_pak_noise_is_rejected(self):
        item = article("Tetra Pak повысила цены в России", "упаковка")
        self.assertFalse(is_relevant(item, self.topic))

    def test_generic_tetra_page_without_geo_is_rejected(self):
        item = article("DMR vs TETRA: key differences", "radio standards comparison", lang="en")
        self.assertFalse(is_relevant(item, self.topic))

    def test_excluded_source_is_rejected(self):
        item = article("TETRA - Wikipedia", "Россия, стандарт", source="Wikipedia", url="https://en.wikipedia.org/wiki/TETRA")
        self.assertFalse(is_relevant(item, self.topic))

    def test_social_username_tetra_is_not_a_tetra_story(self):
        item = article(
            'Tetra 💎 on X: "How to become poorer as a country: invaded by russia..."',
            "nuclear power, war, politics",
            url="https://x.com/TetraspaceWest/status/123",
            source="X",
            kind="web",
            lang="en",
        )
        self.assertFalse(is_relevant(item, self.topic))

    def test_dmr_story_is_relevant(self):
        item = article(
            "Hytera поставила DMR-радиостанции в Россию",
            "контракт на профессиональную радиосвязь",
        )
        self.assertTrue(is_relevant(item, self.topic))
        self.assertEqual(classify(item, self.topic)[0], "technology")

    def test_poc_story_is_relevant(self):
        item = article(
            "PoC-радиосвязь для предприятий",
            "Россия, диспетчерская связь",
        )
        self.assertTrue(is_relevant(item, self.topic))

    def test_trunking_story_is_relevant(self):
        item = article(
            "Транкинговая связь для РЖД",
            "Россия, профессиональная радиосвязь",
        )
        self.assertTrue(is_relevant(item, self.topic))

    def test_broadcast_radio_is_rejected(self):
        item = article(
            "Европа Плюс запустила новую радиостанцию в России",
            "музыкальный эфир",
        )
        self.assertFalse(is_relevant(item, self.topic))

    def test_poc_space_does_not_match_pocket(self):
        item = article(
            "Pocket-sized Ukrainian receiver intercepts Russian drone video",
            "analog video signals sent to Russian operators",
        )
        self.assertFalse(is_relevant(item, self.topic))

    def test_radio_stem_does_not_match_megacorporation(self):
        item = article(
            "В контур российской мегакорпорации могут включить предприятие",
            "отечественной электроники, радиосвязь",
        )
        self.assertFalse(is_relevant(item, self.topic))

    def test_aggregator_leniency_requires_specific_radio_context(self):
        item = article(
            "В контур российской мегакорпорации могут включить предприятие",
            "отечественной электроники, радиосвязь",
            source="Yandex News",
            kind="aggregator",
        )
        item.query = "TETRA радиосвязь"
        self.assertFalse(is_relevant(item, self.topic))

        item = article(
            "Информтехника примет участие в ProfComm 2026",
            "Россия, профессиональная мобильная радиосвязь",
            source="Yandex News",
            kind="aggregator",
        )
        item.query = "TETRA радиосвязь"
        self.assertTrue(is_relevant(item, self.topic))

    def test_global_tcca_article_is_relevant(self):
        item = article(
            "TCCA publishes MCX broadband certification update",
            "critical communications",
            source="TCCA",
            kind="web",
            lang="en",
        )
        self.assertTrue(is_relevant(item, self.topic))

    def test_vendor_name_alone_is_not_enough(self):
        item = article(
            "YADRO выпустила новый сервер",
            "Россия, серверы для дата-центров",
            source="CNews",
        )
        self.assertFalse(is_relevant(item, self.topic))

    def test_specialist_source_bypasses_geo_and_title(self):
        item = article(
            "Private LTE network for ports",
            "industrial connectivity",
            url="https://rcrwireless.com/2026/11/private-lte-ports",
            source="RCR Wireless",
            kind="web",
            lang="en",
        )
        self.assertTrue(is_relevant(item, self.topic))


class DedupeTests(unittest.TestCase):
    def test_paraphrased_story_clusters(self):
        first = article(
            "«Информтехника» выпустила первую российскую портативную TETRA-радиостанцию «МиниКом-АНР-3»",
            "Информтехника, МиниКом-АНР-3, Россия, серийное производство",
            url="https://example.com/1",
        )
        second = article(
            "В России запущено производство первой отечественной портативной TETRA-радиостанции",
            "Информтехника выпустила МиниКом-АНР-3, производство в России",
            url="https://example.com/2",
        )
        groups = group_articles([first, second], ["Информтехника", "МиниКом"])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_unrelated_stories_do_not_cluster(self):
        first = article("Росэл запустил TETRA базовые станции", "Россия, производство", url="https://example.com/1")
        second = article("Росэл открыл микроэлектронный кластер", "Санкт-Петербург, соглашение", url="https://example.com/2", published="2026-06-03T10:00:00+03:00")
        groups = group_articles([first, second], ["Росэл"])
        self.assertEqual(len(groups), 2)


class UrlTests(unittest.TestCase):
    def test_bing_redirect_is_unwrapped(self):
        url = (
            "http://www.bing.com/news/apiclick.aspx?aid=x"
            "&url=https%3A%2F%2Fexample.com%2Fnews%2F1&c=2"
        )
        self.assertEqual(canonicalize_url(url), "https://example.com/news/1")


class BuildDigestSeenTests(unittest.TestCase):
    def setUp(self):
        self.topic = load_topic("ru-tetra")
        self.item = article(
            "«Росэл» выиграл тендер на поставку TETRA-оборудования",
            "Россия, контракт на базовые станции",
            url="https://example.com/seen",
        )
        self.result = SourceResult(source="Media", articles=[self.item])
        self.window = Window(start="2026-04-01T00:00:00+00:00", end="2026-04-30T00:00:00+00:00")
        self.seen = {canonicalize_url(self.item.url): "2026-04-10T10:00:00+00:00"}

    def test_seen_items_are_skipped_by_default(self):
        digest = _build_digest(self.topic, "run", self.window, [self.result], self.seen)
        self.assertEqual(digest.items, [])

    def test_include_seen_keeps_rolling_window_items(self):
        digest = _build_digest(
            self.topic, "run", self.window, [self.result], self.seen, include_seen=True
        )
        self.assertEqual(len(digest.items), 1)
        self.assertEqual(digest.items[0].first_seen, "2026-04-10T10:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
