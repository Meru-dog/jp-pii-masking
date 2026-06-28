"""
synthetic_data.py — 合成日本語テストデータ（C4）【拡張版】。

RDD v1.1 §7.3: 実PIIは使わず、合成データで正解スパン付きの評価セットを構成する。
本拡張は「recall測定の代表性」を主眼に、文脈依存エンティティ
（氏名・住所・組織名・役職・施設名）の表記多様性を厚くする。

各 gold span に任意の variant（表記形ラベル）を付し、AWS 2層（Guardrails/Comprehend）が
「どの表記形で取りこぼすか」を分解測定できるようにする。variant は後方互換のため既定空。

表記の軸（意図的に仕込む難所）:
- 氏名 : 分かち書きあり/なし、ひらがな/カタカナ、役職直結、列挙
- 住所 : 完全形/省略形、ビル・部屋番号付き、改行跨ぎ
- 組織 : 法人格前置/後置/略記(株)、合同会社、官公庁・団体、英字混じり
- 役職 : 一般役職、士業資格、複合役職
- 施設 : ビル名、庁舎、病院、店舗（住所と紛れやすい）

実PIIではない架空値のみを用いる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class GoldSpan:
    start: int
    end: int
    entity_type: str
    text: str
    variant: str = ""  # 表記形ラベル（任意・後方互換）


@dataclass
class Sample:
    name: str
    text: str
    gold: List[GoldSpan]


class _Builder:
    """断片を連結しながら、PII断片の正確なオフセットを記録するヘルパ。"""

    def __init__(self):
        self.parts: List[str] = []
        self.gold: List[GoldSpan] = []
        self._pos = 0

    def add(self, s: str):
        self.parts.append(s)
        self._pos += len(s)
        return self

    def pii(self, s: str, entity_type: str, variant: str = ""):
        start = self._pos
        self.parts.append(s)
        self._pos += len(s)
        self.gold.append(GoldSpan(start, start + len(s), entity_type, s, variant))
        return self

    def build(self, name: str) -> Sample:
        return Sample(name=name, text="".join(self.parts), gold=list(self.gold))


# ---------------------------------------------------------------------------
# 既存3サンプル（v1）— 互換のため維持
# ---------------------------------------------------------------------------

def _sample_contract() -> Sample:
    b = _Builder()
    b.add("業務委託契約書\n\n甲：")
    b.pii("株式会社青空商事", "ORGANIZATION", "kk_prefix")
    b.add("（代表取締役 ")
    b.pii("山田 太郎", "NAME", "spaced")
    b.add("）\n所在地：")
    b.pii("東京都千代田区丸の内一丁目2番3号", "ADDRESS", "full")
    b.add("\n連絡先：")
    b.pii("03-1234-5678", "PHONE", "tokyo")
    b.add("（メール：")
    b.pii("taro.yamada@aozora-example.co.jp", "EMAIL")
    b.add("）\n法人番号：")
    b.pii("1234567890123", "CORPORATE_NUMBER")
    b.add("\n\n乙：")
    b.pii("緑川 花子", "NAME", "spaced")
    b.add("（")
    b.pii("主任", "TITLE", "common")
    b.add("）\n生年月日：")
    b.pii("昭和60年4月1日", "DATE_OF_BIRTH", "wareki")
    b.add("\n個人番号：")
    b.pii("987654321098", "MYNUMBER")
    b.add("\n振込先：")
    b.pii("普通預金1234567", "BANK_ACCOUNT")
    b.add("\n勤務地：")
    b.pii("みなとみらいセンタービル", "FACILITY", "building")
    b.add("\n")
    return b.build("contract")


def _sample_notice() -> Sample:
    b = _Builder()
    b.add("【社内通知】\n\n発信元：")
    b.pii("総務部", "ORGANIZATION", "dept")
    b.add("\n担当：")
    b.pii("佐藤 一郎", "NAME", "spaced")
    b.add(" ")
    b.pii("部長", "TITLE", "common")
    b.add("\n\n下記の社員情報を更新しました。\n氏名：")
    b.pii("田中 美咲", "NAME", "spaced")
    b.add("\n住所：")
    b.pii("大阪府大阪市北区梅田三丁目1番1号", "ADDRESS", "full")
    b.add("\n郵便番号：")
    b.pii("〒530-0001", "POSTAL_CODE")
    b.add("\n電話：")
    b.pii("090-8765-4321", "PHONE", "mobile")
    b.add("\n生年月日：")
    b.pii("1992年12月5日", "DATE_OF_BIRTH", "seireki_kanji")
    b.add("\nクレジットカード：")
    b.pii("4111 1111 1111 1111", "CREDIT_CARD")
    b.add("\n社内システムIP：")
    b.pii("192.168.10.25", "IP_ADDRESS")
    b.add("\n参考URL：")
    b.pii("https://intra.example.co.jp/notice/123", "URL")
    b.add("\n")
    return b.build("notice")


def _sample_memo() -> Sample:
    b = _Builder()
    b.add("打合せメモ\n\n")
    b.pii("国際法務研究所", "ORGANIZATION", "institute")
    b.add("の")
    b.pii("鈴木 健太", "NAME", "spaced")
    b.add("氏（")
    b.pii("弁護士", "TITLE", "shigyo")
    b.add("）と面談。場所は")
    b.pii("帝国ホテル東京", "FACILITY", "hotel")
    b.add("。\n先方の連絡先は ")
    b.pii("kenta.suzuki@kokusai-example.jp", "EMAIL")
    b.add(" および ")
    b.pii("06-2222-3333", "PHONE", "osaka")
    b.add(" 。\n契約相手方：")
    b.pii("令和2年1月15日", "DATE_OF_BIRTH", "wareki")
    b.add(" 設立、法人番号 ")
    b.pii("5566778899001", "CORPORATE_NUMBER")
    b.add(" 。\n")
    return b.build("memo")


# ---------------------------------------------------------------------------
# 追加5サンプル（v2）— 文脈依存の表記多様性を厚くする
# ---------------------------------------------------------------------------

def _sample_litigation() -> Sample:
    """訴訟関連文書（当事者目録ふう）。氏名・住所の列挙、分かち書き有無、省略住所。"""
    b = _Builder()
    b.add("当事者目録\n\n原告\n氏名　")
    b.pii("髙橋 大輔", "NAME", "spaced")
    b.add("\n住所　")
    b.pii("神奈川県横浜市西区みなとみらい二丁目3番5号 ランドマーク802号室", "ADDRESS", "with_building")
    b.add("\n\n被告\n氏名　")
    b.pii("小林優子", "NAME", "nospace")
    b.add("\n住所　")
    b.pii("千代田区霞が関一丁目1番1号", "ADDRESS", "abbrev")
    b.add("\n\n被告補助参加人\n氏名　")
    b.pii("中村 翔", "NAME", "spaced")
    b.add("\n勤務先　")
    b.pii("第一中央法律事務所", "ORGANIZATION", "law_office")
    b.add("\n役職　")
    b.pii("代表弁護士", "TITLE", "compound_shigyo")
    b.add("\n連絡先　")
    b.pii("045-678-9012", "PHONE", "yokohama")
    b.add("\n")
    return b.build("litigation")


def _sample_registry() -> Sample:
    """登記・履歴事項ふう。法人格の前置/後置/略記、官公庁、役員名、本店所在地。"""
    b = _Builder()
    b.add("履歴事項全部証明書（抜粋）\n\n商号　")
    b.pii("みらいテクノロジー株式会社", "ORGANIZATION", "kk_suffix")
    b.add("\n本店　")
    b.pii("東京都港区六本木六丁目10番1号\n六本木ヒルズ森タワー15階", "ADDRESS", "multiline")
    b.add("\n法人番号　")
    b.pii("3010401034567", "CORPORATE_NUMBER")
    b.add("\n設立　令和3年4月1日")  # 設立日: gold非対象（生年月日ではない。regex検出は過剰マスク許容）
    b.add("\n\n役員に関する事項\n代表取締役　")
    b.pii("渡辺 健一", "NAME", "spaced")
    b.add("\n取締役　")
    b.pii("イノウエケンジ", "NAME", "katakana")
    b.add("\n監査役　")
    b.pii("やまもとさちこ", "NAME", "hiragana")
    b.add("\n\n登記所　")
    b.pii("東京法務局港出張所", "ORGANIZATION", "govt")
    b.add("\n")
    return b.build("registry")


def _sample_regulation() -> Sample:
    """社内規程・通知。部署名、多様な役職（複合）、施設名。役職直結氏名を含む。"""
    b = _Builder()
    b.add("情報セキュリティ規程 改定通知\n\n承認　")
    b.pii("代表取締役社長", "TITLE", "compound")
    b.add("　")
    b.pii("斎藤 誠", "NAME", "spaced")
    b.add("\n起案　")
    b.pii("情報システム部", "ORGANIZATION", "dept")
    b.add("　")
    b.pii("課長", "TITLE", "common")
    b.add("　")
    b.pii("森田健", "NAME", "nospace")
    b.add("\n\n適用拠点：")
    b.pii("本社ビル", "FACILITY", "building")
    b.add("、")
    b.pii("大阪支社", "FACILITY", "branch")
    b.add("、")
    b.pii("名古屋データセンター", "FACILITY", "datacenter")
    b.add("\n\n問い合わせ：")
    b.pii("ヘルプデスク係", "ORGANIZATION", "team")
    b.add("（内線 ")
    b.pii("050-1111-2222", "PHONE", "ip_phone")
    b.add("）\n")
    return b.build("regulation")


def _sample_name_focus() -> Sample:
    """氏名バリエーション集中。役職直結、ひらがな・カタカナ、英字混じり組織。"""
    b = _Builder()
    b.add("面談記録\n\n出席者：\n・")
    b.pii("代表取締役", "TITLE", "common")
    b.pii("山田太郎", "NAME", "title_attached")  # 役職直結（切り出しが難しい）
    b.add("\n・")
    b.pii("マイケル・ジョンソン", "NAME", "katakana_foreign")
    b.add("（")
    b.pii("ABCコンサルティング合同会社", "ORGANIZATION", "gk_english_mixed")
    b.add("）\n・")
    b.pii("はせがわ りょう", "NAME", "hiragana")
    b.add("（")
    b.pii("公認会計士", "TITLE", "shigyo")
    b.add("）\n・")
    b.pii("サトウ ユイ", "NAME", "katakana")
    b.add("（")
    b.pii("(株)新和物産", "ORGANIZATION", "kk_abbrev")
    b.add(" ")
    b.pii("執行役員", "TITLE", "common")
    b.add("）\n")
    return b.build("name_focus")


def _sample_address_facility_focus() -> Sample:
    """住所・施設集中。省略形、ビル付き、官公庁庁舎、病院、店舗。"""
    b = _Builder()
    b.add("送付先一覧\n\n1. ")
    b.pii("東京都新宿区西新宿二丁目8番1号", "ADDRESS", "full")
    b.add("（")
    b.pii("東京都庁第一本庁舎", "FACILITY", "govt_bldg")
    b.add("）\n2. ")
    b.pii("中央区銀座四丁目5番6号", "ADDRESS", "abbrev")
    b.add("（")
    b.pii("銀座中央クリニック", "FACILITY", "hospital")
    b.add("）\n3. ")
    b.pii("福岡県福岡市博多区博多駅前三丁目2番1号\nグランドビル7階", "ADDRESS", "multiline")
    b.add("（")
    b.pii("博多駅前店", "FACILITY", "store")
    b.add("）\n担当：")
    b.pii("おおた しんいち", "NAME", "hiragana")
    b.add("\n")
    return b.build("address_facility_focus")


def all_samples() -> List[Sample]:
    return [
        _sample_contract(),
        _sample_notice(),
        _sample_memo(),
        _sample_litigation(),
        _sample_registry(),
        _sample_regulation(),
        _sample_name_focus(),
        _sample_address_facility_focus(),
    ]


# 規則層が唯一の捕捉手段であるエンティティ（§7.4: recall=1.0 を要求）
REGEX_ONLY_ENTITIES = {
    "MYNUMBER", "CORPORATE_NUMBER", "BANK_ACCOUNT",
    "POSTAL_CODE", "DATE_OF_BIRTH", "PHONE", "EMAIL",
    "IP_ADDRESS", "URL", "CREDIT_CARD",
}

# 文脈依存（Guardrails / Comprehend が担当、§7.4: recall>=0.98 目標）
CONTEXTUAL_ENTITIES = {
    "NAME", "ADDRESS", "ORGANIZATION", "TITLE", "FACILITY",
}
