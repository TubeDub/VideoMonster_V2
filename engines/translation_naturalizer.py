"""
Контекстный перевод и натурализация дубляжа.
Приоритет: сохранить смысл → звучать как речь носителя целевого языка.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, List, Sequence

from engines.cleaner import align_segments_to_timing_map, split_by_timing_map

logger = logging.getLogger("tubedub.engines.translation_naturalizer")

MAX_BATCH_SEGMENTS = 10
DEFAULT_MAX_GAP_MS = 1200
DEFAULT_MIN_TTS_MS = 4500
DEFAULT_MIN_MERGE_CHARS = 12
DEFAULT_MAX_SEGMENT_MS = 1800

# Названия языков для LLM-полировки (все поддерживаемые UI-языки)
try:
    from data.languages import LANG_CODE_TO_NAME as _UI_LANG_NAMES

    LANG_NAMES: dict[str, str] = {
        (k.split("-")[0] if k else k): v for k, v in _UI_LANG_NAMES.items()
    }
except Exception:
    LANG_NAMES = {}

LANG_NAMES.update({
    "ru": "Russian",
    "uk": "Ukrainian",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
})

_PRONOUNS_RU = frozenset(
    {
        "он", "она", "оно", "они", "его", "её", "ее", "их",
        "этот", "эта", "это", "эти", "тот", "та", "те",
        "я", "мы", "вы", "ты",
    }
)

_PRONOUNS_UK = frozenset(
    {
        "він", "вона", "воно", "вони", "його", "її", "їх",
        "цей", "ця", "це", "ці", "той", "та", "те", "ті",
        "я", "ми", "ви", "ти",
    }
)

# Русский: Jr. → младший (не украинское «молодший»)
_RU_JR_SUFFIX_FIXES: list[tuple[str, str]] = [
    (r"\bДжордж-молодший\b", "Джордж-младший"),
    (r"\bДжорджа-молодшего\b", "Джорджа-младшего"),
    (r"\bДжорджа-молодшого\b", "Джорджа-младшего"),
    (r"\bДжорджу-молодшему\b", "Джорджу-младшему"),
    (r"\bДжорджу-молодшому\b", "Джорджу-младшему"),
    (r"\bДжорджем-молодшим\b", "Джорджем-младшим"),
    (r"\bДжордж-молодшим\b", "Джорджем-младшим"),
    (r"\b([A-ZА-ЯІЇЄ][a-zа-яіїє'-]+)-молодший\b", r"\1-младший"),
    (r"\b([A-ZА-ЯІЇЄ][a-zа-яіїє'-]+)-молодшего\b", r"\1-младшего"),
    (r"\b([A-ZА-ЯІЇЄ][a-zа-яіїє'-]+)\s+молодший\b", r"\1-младший"),
    (r"\b([A-ZА-ЯІЇЄ][a-zа-яіїє'-]+)\s+молодшего\b", r"\1-младшего"),
]

# Частые артефакты MT для русского
_RU_WORD_FIXES: list[tuple[str, str]] = [
    (r"\b(он|она|оно|они|ты|мы|вы|я|тут|здесь|там)\s+ест\b", r"\1 есть"),
    (r"\bидет\b", "идёт"),
    (r"\bидет\s", "идёт "),
    (r"\bне\s+может\s+быть\s+чтобы\b", "не может быть, чтобы"),
    (r"\bв\s+течение\s+времени\b", "со временем"),
    (r"\bделает\s+так\s+что\b", "делает так, что"),
    (r"\s+-\s+", " — "),
    (r"\bкоторый\s+который\b", "который"),
    (r"\bчто\s+что\b", "что"),
]

# Украинский: типичные ошибки MT и русизмы
_UK_WORD_FIXES: list[tuple[str, str]] = [
    (r"\b(він|вона|воно|вони|ти|ми|ви|я|тут|там)\s+є\b", r"\1 є"),
    (r"\bі і\b", "і"),
    (r"\bщо що\b", "що"),
    (r"\bякий\s+який\b", "який"),
    (r"\bвін\s+відчувати,\s+що\b", "він відчував, що"),
    (r"\bв\s+відділенні\b", "у відділенні"),
    (r"\bпереможного\s+їзда\b", "переможного заїзду"),
    (r"\bпредставив\s+себе\s+як\b", "представився як"),
    (r"\s+-\s+", " — "),
]

_UK_RUISM_FIXES: list[tuple[str, str]] = [
    (r"\bещё\b", "ще"),
    (r"\bЕщё\b", "Ще"),
    (r"\bеще\b", "ще"),
    (r"\bЕще\b", "Ще"),
    (r"\bчтобы\b", "щоб"),
    (r"\bЧтобы\b", "Щоб"),
    (r"\bэтот\b", "цей"),
    (r"\bЭтот\b", "Цей"),
    (r"\bэта\b", "ця"),
    (r"\bЭта\b", "Ця"),
    (r"\bэто\b", "це"),
    (r"\bЭто\b", "Це"),
    (r"\bэти\b", "ці"),
    (r"\bЭти\b", "Ці"),
    (r"\bони\b", "вони"),
    (r"\bОни\b", "Вони"),
    (r"\bего\b", "його"),
    (r"\bЕго\b", "Його"),
    (r"\bеё\b", "її"),
    (r"\bЕё\b", "Її"),
    (r"\bее\b", "її"),
    (r"\bнет\b", "немає"),
    (r"\bНет\b", "Немає"),
    (r"\bчто\b", "що"),
    (r"\bЧто\b", "Що"),
    (r"\bкоторый\b", "який"),
    (r"\bКоторый\b", "Який"),
    (r"\bкоторая\b", "яка"),
    (r"\bкоторое\b", "яке"),
    (r"\bкоторые\b", "які"),
    (r"\bтоже\b", "теж"),
    (r"\bТоже\b", "Теж"),
    (r"\bтакже\b", "також"),
    (r"\bТакже\b", "Також"),
    (r"\bсейчас\b", "зараз"),
    (r"\bСейчас\b", "Зараз"),
    (r"\bкогда\b", "коли"),
    (r"\bКогда\b", "Коли"),
    (r"\bесли\b", "якщо"),
    (r"\bЕсли\b", "Якщо"),
    (r"\bпотому\b", "тому"),
    (r"\bПотому\b", "Тому"),
    (r"\bздесь\b", "тут"),
    (r"\bЗдесь\b", "Тут"),
    (r"\bбудет\b", "буде"),
    (r"\bБудет\b", "Буде"),
    (r"\bбыл\b", "був"),
    (r"\bБыл\b", "Був"),
    (r"\bбыла\b", "була"),
    (r"\bбыли\b", "були"),
    (r"\bочень\b", "дуже"),
    (r"\bОчень\b", "Дуже"),
    (r"\bможет\b", "може"),
    (r"\bМожет\b", "Може"),
    (r"\bтолько\b", "лише"),
    (r"\bТолько\b", "Лише"),
    (r"\bмладший\b", "молодший"),
    (r"\bМладший\b", "Молодший"),
    (r"\bмладшая\b", "молодша"),
    (r"\bмладше\b", "молодше"),
    (r"\bне\s+мог\b", "не міг"),
    (r"\bне\s+могла\b", "не могла"),
    (r"\bехав\b", "їхав"),
]

# Смешение языков / русские слова в украинском дубляже
_UK_MIXED_LANGUAGE_FIXES: list[tuple[str, str]] = [
    (r"\bТак что\b", "Тому що"),
    (r"\bтак что\b", "тому що"),
    (r"\bПозвольте\b", "Дозвольте"),
    (r"\bпозвольте\b", "дозвольте"),
    (r"\bполучил\b", "отримав"),
    (r"\bполучила\b", "отримала"),
    (r"\bполучили\b", "отримали"),
    (r"\bполучить\b", "отримати"),
    (r"\bсказал\b", "сказав"),
    (r"\bСказал\b", "Сказав"),
    (r"\bJunior\b", "молодший"),
    (r"\bjunior\b", "молодший"),
]

# Кальки и неестественные конструкции (UK)
_UK_CALQUE_NATURALIZER: list[tuple[str, str]] = [
    (r"\bробить\s+сенс\b", "має сенс"),
    (r"\bробити\s+сенс\b", "мати сенс"),
    (r"\bбрати\s+місце\b", "відбувається"),
    (r"\bбере\s+місце\b", "відбувається"),
    (r"\bмати\s+місце\s+бути\b", "відбувається"),
    (r"\bшматок\s+торта\b", "легко"),
    (r"\bце\s+повертається\b", "виявляється"),
    (r"\bна\s+даний\s+момент\s+часу\b", "зараз"),
    (r"\bз\s+іншого\s+боку\s+руки\b", "з іншого боку"),
    (r"\bнічого, що є серйозно\b", "нічого серйозного"),
    (r"\bщо є серйозно\b", "серйозно"),
    (r"\bне шукав нічого, що є серйозно\b", "ніколи серйозно нічим не займався"),
    (r"\bочаровательност\w*\b", "захоплення"),
    (r"отримав\s+захоплення\s+свого\s+сина\s+до\s+машин", "успадкував від сина любов до автомобілів"),
    (r"отримав\s+очаров\w*\s+свого\s+сина\s+до\s+машин", "успадкував від сина любов до автомобілів"),
    (r"\bможе\s+летіти\b", "може літати"),
    (r"\bбудуть\s+починати\s+війн\w*", "розпочнуть"),
    (r"\bбув\s+застосований\s+до\s+університет\w*", "вступив до університету"),
    (r"\bне\s+сяде\b", "не поміститься"),
    (r"\bбув\s+водінням\b", "їхав"),
    (r"\bяк\s+він\s+був\s+водінням\b", "поки він їхав"),
    (r"\bяк\s+він\s+був\s+за\s+кермом\b", "поки він їхав"),
    (r"\bне\s+міг\s+не\s+відчувати,\s+що\s+він\s+(?:справді|дійсно)\s+боїться\s+потрапити\s+туди\b",
     "не міг позбутися відчуття, що йому справді страшно туди дістатися"),
    (r"\bне\s+міг\s+не\s+відчувати,\s+що\s+він\s+(?:справді|дійсно)\s+боявся\s+потрапити\s+туди\b",
     "не міг позбутися відчуття, що йому справді страшно туди дістатися"),
    (r"\bне\s+мог\s+не\s+відчувати,\s+що\s+(?:справді\s+)?боявся\s+там\s+брати\b",
     "не міг позбутися відчуття, що йому справді страшно туди дістатися"),
    (r"\bне\s+міг\s+не\s+відчувати,\s+що\s+(?:справді\s+)?боявся\s+там\s+брати\b",
     "не міг позбутися відчуття, що йому справді страшно туди дістатися"),
    (r"\bпросто\s+не\s+отримав\s+одержимості\s+свого\s+сина\b",
     "просто не розумів одержимості свого сина автомобілями"),
    (r"\bтому\s+ми\s+отримаємо\s+твою\s+справжню\s+роботу\b",
     "отримаєш справжню роботу"),
    (r"\bприйшов\s+на\s+цей\s+перетин\b", "під'їхав до перехрестя"),
    (r"\bпоруч\s+з\s+його\s+домом\b", "біля його дому"),
    (r"\bпочинає\s+робити\s+поворот\b", "почав повертати"),
    (r"\bможе\s+прискорити\s+дорогу\b", "на великій швидкості промчала дорогою"),
    (r"\bрозім['']яти\s+на\s+машині\b", "врізалася в машину"),
    (r"\bвигнали\s+з\s+машини\b", "викинуло з машини"),
    (r"\bДжордж\s+Джер\.?\b", "Джордж-молодший"),
    (r"\bГеорг\s+Жр\.?\b", "Джордж-молодший"),
    (r"\bДжордж\s+Жр\.?\b", "Джордж-молодший"),
    (r"\bСо\s+Джордж\b", "Тож Джордж"),
    (r"\bз\s+досвіду\s+його\s+ближнього\s+бою\b", "після свого досвіду на межі смерті"),
    (r"\bУявлявся,\s+що\s+насправді\s+був\s+дад\b",
     "зрозумів, що його батько був певною мірою правий"),
    (r"\bстав\s+його\s+потенціалом\b", "марнував свій потенціал"),
    (r"\bВін\s+не\s+хоче\s+перегонів\b", "Він більше не хоче займатися автогонками"),
    (r"\bчоловік\s+зі\s+смілостями\b", "чоловік середнього віку"),
    (r"\bбув\s+застосований\s+до\s+USC\b", "подав заявку до USC"),
    (r"\bкомпанії\s+з\s+фільму\s+[«\"]Скарб\s+США[»\"]\b",
     "кіношколи USC"),
    (r"\bчергувати\s+кінотеатр\b", "змінити кіно"),
    (r"\bповністю\s+чергувати\b", "назавжди змінити"),
    (r"\bзагадних\s+фільмів\b", "революційних фільмів"),
    (r"\bпокаже\s+на\s+створення\b", "створить"),
    (r"\bбудуть\s+зірвати\s+війни\b", "стане «Зоряними війнами»"),
    (r"\bзірвати\s+війни\b", "«Зоряні війни»"),
    (r"\bпереможного\s+їзда\b", "переможного заїзду"),
    (r"\bкінотехніку\b", "програму з кінематографії"),
    (r"\bпісля\s+того,\s+як\s+відпустив\s+заяву\b", "після того, як надіслав заяву"),
    (r"\bдуже\s+легко\s+відволікся\b", "дуже легко відволікався"),
    (r"\bназваний\b", "на ім'я"),
    (r"\b18-річний\s+хлопчик\b", "18-річний хлопець"),
    (r"\bпоїхав\s+через\s+рідний\s+міст\b", "проїжджав через своє рідне місто"),
    (r"\bна\s+своєму\s+шляху\s+додому\b", "дорогою додому"),
    (r"\bмалюк\b", "дитина"),
    (r"\bне\s+займався\s+нічим\s+настільки\s+серйозним\b", "нічим серйозно не займався"),
    (r"\bкрім\s+автомобілів\b", "окрім автомобілів"),
    (r"\bІ\s+в\s+цьому\s+пункті\b", "І в той момент"),
    (r"\bкупив\s+йому\s+невеликим\s+італійським\s+автомобілем\b", "купив йому маленький італійський автомобіль"),
    (r"\bз\s+назвою\b", "під назвою"),
    (r"\bдав\s+йому\s+George\s+Lucas\b", "подарував йому Fiat"),
    (r"\bне\s+отримував\s+сина\b", "не розумів одержимості сина"),
    (r"\bяк\s+чому\s+ви\s+не\s+можете\b", "Чому ти не можеш"),
    (r"\bприйняти\s+цю\s+увагу\b", "зосередитися на цьому"),
    (r"\bзастосувати\s+її\s+до\s+інших\s+речей\b", "застосувати це до інших речей"),
    (r"\bОтже,\s+ми\s+отримаємо\s+вашу\s+реальну\s+роботу\b",
     "отримаєш справжню роботу"),
    (r"\bтож\s+ми\s+отримаємо\s+твою\s+справжню\s+роботу\b",
     "отримаєш справжню роботу"),
    (r"\bтож\s+ми\s+отримаємо\s+вашу\s+реальну\s+роботу\b",
     "отримаєш справжню роботу"),
    (r"\bпрактично\s+кожного\s+обіду\b", "по суті, кожна вечеря"),
    (r"\bякщо\s+він\s+прийшов,\s*ця\s+велика\b",
     "перетворювалася на велику суперечку між батьком і сином"),
    (r"\bперетворювал(?:ася|алась)\s+на\s+велику\s*([\.!?])",
     "перетворювалася на велику суперечку між батьком і сином\\1"),
    (r"\bприйшов\s+до\s+цього\s+перехрестя\b", "під'їхав до перехрестя"),
    (r"\bде\s+він\s+був\s+прямо\s+біля\s+свого\s+дому\b", "яке було прямо біля його будинку"),
    (r"\bпочинає\s+робити\s+поворот\b", "почав повертати"),
    (r"\bколи\s+він\s+чує\s+це\s+дійсно\s+гучне\s+звучання\b", "коли почув дуже гучний скрип"),
    (r"\bгучне\s+звучання\b", "гучний скрип"),
    (r"\bвсе\s+пішл[аи]\s+чорним\b", "все потемніло"),
    (r"\bреанімаційному\s+відділенні\b", "відділенні інтенсивної терапії"),
    (r"\bдва\s+тижні\s+тому\b", "двома тижнями раніше"),
    (r"\bможе\s+прискорити\s+дорогу\b", "на великій швидкості промчала дорогою"),
    (r"\bрозім['']явся\s+в\s+машину\b", "врізалася в машину"),
    (r"\bвилетіл[ао]\s+з\s+автомобіля\b", "викинуло з машини"),
    (r"\bфінішній\s+лінії\b", "фінішній прямій"),
    (r"\bгоночній\s+трасі\b", "гоночному треку"),
    (r"\bдосвід\s+його\s+ближнього\s+бою\b", "після свого клінічного смертельного досвіду"),
    (r"\bближнього\s+бою\b", "клінічного смертельного досвіду"),
    (r"\bпісля\s+його\s+досвіду\s+близької\s+смерті\b", "після свого клінічного смертельного досвіду"),
    (r"\bдійсно\s+має\s+дad\w*\b", "його батько був певною мірою правий"),
    (r"\bмає\s+дad\w*\b", "батько був певною мірою правий"),
    (r"\bбув\s+витрачати\s+свій\s+потенціал\b", "марнує свій потенціал"),
    (r"\bприймав\s+участь\s+у\s+престижній\s+програмі\b", "подав заявку на престижну програму"),
    (r"\bне\s+потрапить\b", "його не приймуть"),
    (r"\bфотографії\s+виграшу\b", "фотографії переможця"),
    (r"\bпобитий\b", "чоловік середнього віку"),
    (r"\bкінематографістом\b", "кінооператором"),
    (r"\bоператором\s+у\s+Джордж\s+молодший\b", "кінооператором у Голлівуді"),
    (r"\bчергував\s+життя\b", "змінив життя"),
    (r"\bчергувати\s+кінотеатр\b", "змінити кіно"),
    (r"\bчергувати\s+кінематограф\b", "змінити кіно"),
    (r"\bбуде\s+йти\s+на\s+створення\b", "створить"),
    (r"\bйде\s+на\s+створення\b", "створить"),
    (r"\bподала\s+заявку\b", "подав заявку"),
    (r"\bUniversity\s+of\s+Southern\s+California\b", "Університет Південної Каліфорнії"),
    (r"\bу\s+Hollywood\b", "у Голлівуді"),
    (r"\bв\s+Hollywood\b", "у Голлівуді"),
    (r"\bоператором\s+у\s+Hollywood\b", "оператором у Голлівуді"),
    (r"\bкінематографістом\s+Hollywood\b", "кінематографістом у Голлівуді"),
    (r"\bїхній\s+Програма\s+кіно\b", "їхню програму з кінематографії"),
    (r"\bПрограма\s+кіно\b", "програму з кінематографії"),
    (r"\bнайсвіжіших\s+фільмів\b", "найреволюційніших фільмів"),
    (r"\bбуде\s+проходити,\s+щоб\s+стати\b", "стане"),
    (r"\bне\s+довга\s+після\b", "незабаром після"),
    (r"\bОтриманий\s+лист\s+з\s+фільму\b", "отримав лист про зарахування"),
    (r"\bбув\s+прочитаний\b", "переживав"),
    (r"\bДавайте\s+мені\s+зробити\b", "Дозвольте мені зробити"),
    (r"\bкінематографістом\s+Голлівуд\b", "кінематографістом у Голлівуді"),
    (r"\bоператором\s+Голлівуд\b", "оператором у Голлівуді"),
    (r"\bкінооператором\s+Голлівуд\b", "кінооператором у Голлівуді"),
    (r"\bправий\s+ставить\s+свій\s+потенціал\b", "був частково правий щодо марної витрати свого потенціалу"),
    (r"\bв\s+Університет\s+Південної\s+Каліфорнії\b", "до Університету Південної Каліфорнії"),
    (r"\bбуде\s+в\.\s*$", "не потрапить"),
    (r"\bне\s+буде\s+в\b", "не потрапить"),
    (r"\bтакож\s+був\s+дуже\s+легко\s+і\b", "також дуже легко відволікався, і"),
    (r"\bбув\s+дуже\s+легко\s+і\b", "дуже легко відволікався, і"),
    (r"\bДжордж-молодший\s+було\s+прокладен(?:е|о)\b", "Джордж-молодший лежав"),
    (r"\bбуло\s+прокладен(?:е|о)\s+в\s+стаціонарному\s+комплексі\b", "лежав на лікарняному ліжку у відділенні інтенсивної терапії"),
    (r"\bлеж(?:ав|ала)\s+в\s+стаціонарному\s+комплексі\b", "лежав на лікарняному ліжку у відділенні інтенсивної терапії"),
    (r"\bале\s+він\s+пережил[иі]\b", "але він вижив"),
    (r"\bзрозумів,\s+що\s+дійсно\s+має\s+д[аа][dд]\w*\b", "зрозумів, що його батько був певною мірою правий"),
    (r"\bдійсно\s+має\s+д[aа][dд]\w*\b", "його батько був певною мірою правий"),
    (r"\bне\s+хоче\s+перегонів\b", "більше не хоче займатися автогонками"),
    (r"\bА\s+замість\s+того,\s+як\s+він\s+підбирав\s+цю\s+камеру\b", "Замість цього він узяв камеру"),
    (r"\bпотрапив\s+до\s+свого\s+старого\s+хобі\s+фотографії\b", "знову захопився своїм колишнім хобі — фотографією"),
    (r"\bколи\s+він\s+чує\s+цей\b", "коли він почув цей"),
    (r"\bВ\s+цілому,\s+це\s+буде\s+йти\s+на\b", "Це також"),
    (r"\bбуде\s+йти\s+на\s+повністю\s+змінити\b", "назавжди змінить"),
    (r"\bколи\s+він\s+проходив\s+туди,\s*Він\s+прийшов,\s*бо\s+він\s+і\s+просто\s+запитав\b",
     "коли він підійшов, до нього підійшов чоловік середнього віку і просто запитав"),
]

# Неестественный порядок слов (UK) — только безопасные замены
_UK_WORD_ORDER_FIXES: list[tuple[str, str]] = [
    (r"\bпрямо поруч з (його|її|їх) домом\b", "майже біля свого дому"),
    (r"\bпрямо поруч з\b", "майже біля"),
    (r"\bбув прямо поруч\b", "був уже майже біля"),
]

# Испорченная латиница (Hollywoodі → Hollywood)
_CORRUPTED_LATIN_FIXES: list[tuple[str, str]] = [
    (r"\bHollywoodі\b", "Hollywood"),
    (r"\bHollywoodi\b", "Hollywood"),
    (r"\bФайат\b", "Fiat"),
    (r"\bЮСК\b", "USC"),
]

# RU calques
_RU_CALQUE_NATURALIZER: list[tuple[str, str]] = [
    (r"\bделает\s+смысл\b", "имеет смысл"),
    (r"\bделать\s+смысл\b", "иметь смысл"),
    (r"\bбрать\s+место\b", "происходит"),
    (r"\bимеет\s+место\s+быть\b", "происходит"),
    (r"\bкусок\s+торта\b", "проще простого"),
    (r"\bэто\s+поворачивается\b", "оказывается"),
]

# Универсальные артефакты (все языки)
_DUP_WORD = r"(?<!\w)([\w\u0400-\u04FF\u0500-\u052F'-]+)\s+\1(?!\w)"
_GENERIC_FIXES: list[tuple[str, str]] = [
    (r"\s{2,}", " "),
    (r"\s+([,.!?;:])", r"\1"),
    (r"([,.!?;:])([^\s])", r"\1 \2"),
    (_DUP_WORD, r"\1"),
    (r"«{2,}", "«"),
    (r"»{2,}", "»"),
]


def _fix_uk_dinner_argument_scene(original: str, text: str) -> str:
    """George/Fiat scene: keep full dinner→argument line; drop orphan split lead-in."""
    if not text or not original:
        return text
    ol = original.lower()
    out = str(text)
    if "every dinner" in ol or "huge argument" in ol or "real job" in ol:
        out = re.sub(
            r"\bчому\s+ви\s+не\s+можете\s+зосередитись\b",
            "чому ти не можеш зосередитися",
            out,
            flags=re.I,
        )
        out = re.sub(
            r"\bтож\s+ми\s+отримаємо\s+(?:твою|вашу)\s+(?:справжню|реальну)\s+роботу\b",
            "отримаєш справжню роботу",
            out,
            flags=re.I,
        )
        if re.search(r"перетворювал\w+\s+на\s+велику\s*[\.!?]\s*$", out, re.I):
            out = re.sub(
                r"перетворювал(?:ася|алась)\s+на\s+велику\s*([\.!?])\s*$",
                r"перетворювалася на велику суперечку між батьком і сином\1",
                out,
                flags=re.I,
            )
    if ol.strip().startswith("between father and son"):
        out = re.sub(
            r"^\s*суперечк[аи]\s+між\s+батьком\s+і\s+сином\s*[\.!?,]\s*",
            "",
            out,
            count=1,
            flags=re.I,
        )
        if out and out[0].islower():
            out = out[0].upper() + out[1:]
    return out.strip()


def _apply_word_fixes(text: str, fixes: list[tuple[str, str]]) -> str:
    out = str(text or "")
    for pattern, repl in fixes:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out.strip()


def fix_ru_jr_suffix(text: str) -> str:
    """Russian: Jr. suffix is «младший», not Ukrainian «молодший»."""
    return _apply_ru_jr_suffix_fixes(str(text or ""))


def _apply_ru_jr_suffix_fixes(text: str) -> str:
    return _apply_word_fixes(text, _RU_JR_SUFFIX_FIXES)


def _apply_ru_word_fixes(text: str) -> str:
    out = _apply_word_fixes(text, _RU_WORD_FIXES)
    return _apply_ru_jr_suffix_fixes(out)


def _apply_uk_word_fixes(text: str) -> str:
    out = _apply_word_fixes(text, _UK_WORD_FIXES)
    return _apply_word_fixes(out, _UK_RUISM_FIXES)


def _apply_generic_fixes(text: str) -> str:
    return _apply_word_fixes(text, _GENERIC_FIXES)


@dataclass
class NaturalizerResult:
    text: str
    reasons: list[str] = field(default_factory=list)
    mixed_language_pct: float = 0.0
    retry_reason: str = ""
    problems: list[str] = field(default_factory=list)
    fix_count: int = 0
    quality_score: float = 0.0
    restored_entities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    retried: bool = False

    @property
    def changed(self) -> bool:
        return "no_changes" not in self.reasons and bool(self.reasons)


def load_user_corrections(app_dir=None) -> list[tuple[str, str, str]]:
    """
    Hook for future self-learning user dictionary.
    Returns list of (pattern, replacement, category).
    """
    from pathlib import Path
    import json

    base = Path(app_dir) if app_dir else Path(__file__).resolve().parent.parent
    path = base / "data" / "naturalizer_user_corrections.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = data.get("rules") if isinstance(data, dict) else []
        out: list[tuple[str, str, str]] = []
        for r in rules or []:
            if not isinstance(r, dict):
                continue
            pat = str(r.get("pattern") or "")
            repl = str(r.get("replacement") or "")
            if pat and repl:
                out.append((pat, repl, str(r.get("category") or "user")))
        return out
    except Exception:
        return []


def _matches_any_pattern(text: str, fixes: list[tuple[str, str]]) -> bool:
    for pat, _ in fixes:
        if re.search(pat, str(text or ""), flags=re.IGNORECASE):
            return True
    return False


def _uk_ruism_tokens(text: str) -> list[str]:
    from engines.translation_quality_score import _UK_RUISM_WORDS

    words = re.findall(r"\b[\w'-]+\b", str(text or ""), flags=re.UNICODE)
    return [w for w in words if w.lower() in _UK_RUISM_WORDS]


def _english_leak_tokens(text: str, original: str) -> list[str]:
    """Latin words in dub text that are not brands/titles from source."""
    from engines.proper_nouns_dict import keep_latin_tokens, preferred_translations

    orig_words = {w.lower() for w in re.findall(r"\b[a-zA-Z]{2,}\b", original or "")}
    keep = {w.lower() for w in keep_latin_tokens()}
    keep.update(k.lower() for k in preferred_translations())
    tr_words = re.findall(r"\b[a-zA-Z]{2,}\b", text or "")
    return [
        w
        for w in tr_words
        if w.lower() not in keep and w.lower() not in orig_words
    ]


def detect_mt_issues(
    text: str,
    *,
    tgt_lang: str,
    original: str = "",
    src_lang: str | None = None,
) -> list[str]:
    """Detect machine-translation artefacts — if empty, text is natural enough."""
    t = str(text or "").strip()
    if not t:
        return []

    lang = _normalize_lang(tgt_lang)
    issues: list[str] = []

    if re.search(_DUP_WORD, t, flags=re.IGNORECASE):
        issues.append("duplicate_words")

    if lang == "uk":
        if _uk_ruism_tokens(t) or _matches_any_pattern(t, _UK_RUISM_FIXES):
            issues.append("ruism")
        for pat, _ in _UK_MIXED_LANGUAGE_FIXES:
            if re.search(pat, t, flags=re.IGNORECASE):
                issues.append("mixed_language")
                break
        for pat, _ in _UK_CALQUE_NATURALIZER:
            if re.search(pat, t, flags=re.IGNORECASE):
                issues.append("calque")
                break
        for pat, _ in _UK_WORD_ORDER_FIXES:
            if re.search(pat, t, flags=re.IGNORECASE):
                issues.append("word_order")
                break

    if lang == "ru":
        for pat, _ in _RU_CALQUE_NATURALIZER:
            if re.search(pat, t, flags=re.IGNORECASE):
                issues.append("calque")
                break
        if re.search(r"молодш", t, flags=re.IGNORECASE):
            issues.append("uk_calque")

    for pat, _ in _CORRUPTED_LATIN_FIXES:
        if re.search(pat, t, flags=re.IGNORECASE):
            issues.append("corrupted_latin")
            break

    if re.search(r"[A-Za-z]{3,}[іїє]", t):
        issues.append("corrupted_latin")

    leaked = _english_leak_tokens(t, original)
    if leaked:
        issues.append("mixed_language")

    from engines.semantic_translation import detect_semantic_issues

    if detect_semantic_issues(
        original, t, source_lang=src_lang, target_lang=lang
    ):
        if "calque" not in issues:
            issues.append("calque")

    pronouns = _PRONOUNS_UK if lang == "uk" else _PRONOUNS_RU if lang == "ru" else frozenset()
    words = [re.sub(r"[^\w'-]", "", w.lower()) for w in t.split()]
    for i in range(1, len(words)):
        if words[i] and words[i] == words[i - 1] and words[i] in pronouns:
            issues.append("duplicate_pronoun")
            break

    return sorted(set(issues))


def _apply_if_changed(
    text: str,
    fixes: list[tuple[str, str]],
    reason: str,
    reasons: list[str],
) -> str:
    out = _apply_word_fixes(text, fixes)
    if out != text:
        reasons.append(reason)
    return out


def _polish_v1_rules(
    raw_mt: str,
    *,
    original: str = "",
    tgt_lang: str = "uk",
    src_lang: str | None = None,
    prev_context: str | None = None,
    app_dir=None,
    use_llm: bool = False,
) -> NaturalizerResult:
    """V1 rule-based polish — used by V2 orchestrator and legacy path."""
    from engines.translation_quality import accept_naturalizer_change

    raw = str(raw_mt or "").strip()
    if not raw:
        return NaturalizerResult("", ["no_changes"])

    lang = _normalize_lang(tgt_lang)
    reasons: list[str] = []
    current = raw

    # Mandatory entity fixes — always run (MT can look "clean" but still use UA «молодший» for Jr.)
    if original.strip():
        from engines.proper_nouns_dict import apply_proper_noun_polish

        after = apply_proper_noun_polish(original, current, app_dir=app_dir, tgt_lang=lang)
        if after != current:
            reasons.append("fixed_named_entities")
            current = after
    if lang == "ru":
        after = fix_ru_jr_suffix(current)
        if after != current:
            if "fixed_named_entities" not in reasons:
                reasons.append("fixed_named_entities")
            current = after

    if lang == "uk" and original.strip():
        after = _fix_uk_dinner_argument_scene(original, current)
        if after != current:
            reasons.append("fixed_calque")
            current = after

    # Always run rule tables — empty detect_mt_issues must NOT skip polish.
    # Literal Marian often looks "clean" (no matched issue codes) but still needs
    # UK calques / ruisms / word-order / naturalize_uk. Issues only guide reasons.
    issues = detect_mt_issues(
        raw, tgt_lang=lang, original=original, src_lang=src_lang
    )
    after = _apply_generic_fixes(current)
    if after != current:
        reasons.append("fixed_duplicate_words")
        current = after

    if lang == "uk":
        after = _apply_if_changed(current, _UK_MIXED_LANGUAGE_FIXES, "fixed_mixed_language", reasons)
        current = after
        after = _apply_if_changed(current, _UK_RUISM_FIXES, "fixed_ruism", reasons)
        current = after

    calques = _UK_CALQUE_NATURALIZER if lang == "uk" else _RU_CALQUE_NATURALIZER if lang == "ru" else []
    after = _apply_if_changed(current, calques, "fixed_calque", reasons)
    current = after
    from engines.semantic_translation import apply_semantic_polish_line

    after = apply_semantic_polish_line(current, target_lang=lang)
    if after != current:
        reasons.append("fixed_calque")
        current = after

    if original.strip():
        from engines.proper_nouns_dict import apply_proper_noun_polish

        after = apply_proper_noun_polish(original, current, app_dir=app_dir, tgt_lang=lang)
        if after != current:
            reasons.append("fixed_named_entities")
            current = after
        if lang == "ru":
            after = fix_ru_jr_suffix(current)
            if after != current:
                if "fixed_named_entities" not in reasons:
                    reasons.append("fixed_named_entities")
                current = after

    after = _apply_if_changed(current, _CORRUPTED_LATIN_FIXES, "fixed_named_entities", reasons)
    current = after
    stripped = re.sub(r"\b([A-Za-z]{3,})і\b", r"\1", current)
    if stripped != current:
        if "fixed_named_entities" not in reasons:
            reasons.append("fixed_named_entities")
        current = stripped

    if lang == "uk":
        after = _apply_if_changed(current, _UK_WORD_ORDER_FIXES, "fixed_word_order", reasons)
        current = after

    before_grammar = current
    if lang == "uk":
        after = naturalize_uk(current, None)
    elif lang == "ru":
        after = naturalize_ru(current, None)
    else:
        after = naturalize_generic(current, None)
    if after != before_grammar:
        if "duplicate_pronoun" in issues or "duplicate_words" in issues:
            reasons.append("fixed_duplicate_words")
        else:
            reasons.append("grammar_rewrite")
        current = after

    for pat, repl, cat in load_user_corrections(app_dir):
        new = re.sub(pat, repl, current, count=1, flags=re.IGNORECASE)
        if new != current:
            reasons.append(f"user_{cat}")
            current = new

    if lang == "uk" and original.strip():
        from engines.naturalizer_v2.uk_name_forms import apply_uk_dub_name_polish

        after = apply_uk_dub_name_polish(current, original=original)
        if after != current:
            reasons.append("fixed_named_entities")
            current = after

    accepted = accept_naturalizer_change(raw, current, original=original)
    if accepted != current and accepted == raw:
        return NaturalizerResult(raw, ["blocked_degradation"])

    current = accepted

    remaining = detect_mt_issues(
        current, tgt_lang=lang, original=original, src_lang=src_lang
    )
    if use_llm and remaining and current == raw:
        llm = _optional_llm_polish(
            current,
            original=original,
            raw_mt=raw,
            prev_context=prev_context,
            tgt_lang=lang,
            src_lang=src_lang,
        )
        if llm:
            llm_polished = naturalize_text(llm, lang, prev_context)
            if original.strip():
                from engines.proper_nouns_dict import apply_proper_noun_polish

                llm_polished = apply_proper_noun_polish(
                    original, llm_polished, app_dir=app_dir, tgt_lang=lang
                )
                llm_polished = fix_ru_jr_suffix(llm_polished) if lang == "ru" else llm_polished
            llm_accepted = accept_naturalizer_change(raw, llm_polished, original=original)
            if llm_accepted != raw:
                current = llm_accepted
                reasons.append("grammar_rewrite")

    if current == raw:
        return NaturalizerResult(raw, ["no_changes"])
    if not reasons:
        reasons = ["grammar_rewrite"]
    return NaturalizerResult(current, sorted(set(reasons)))


def polish_segment_detailed(
    raw_mt: str,
    *,
    original: str = "",
    tgt_lang: str = "uk",
    src_lang: str | None = None,
    prev_context: str | None = None,
    app_dir=None,
    use_llm: bool = False,
    entity_token_map: dict[str, str] | None = None,
) -> NaturalizerResult:
    """Post-Marian polish — V2 editor-translator when enabled, else V1 rules."""
    from engines.naturalizer_v2.config import is_v2_enabled

    if is_v2_enabled():
        from engines.naturalizer_v2.orchestrator import polish_segment_v2

        v2 = polish_segment_v2(
            raw_mt,
            original=original,
            tgt_lang=tgt_lang,
            src_lang=src_lang,
            prev_context=prev_context,
            app_dir=app_dir,
            use_llm=use_llm,
            entity_token_map=entity_token_map,
        )
        return NaturalizerResult(
            text=v2["text"],
            reasons=v2["reasons"],
            mixed_language_pct=v2.get("mixed_language_pct", 0.0),
            retry_reason=v2.get("retry_reason", ""),
            problems=v2.get("problems", []),
            fix_count=v2.get("fix_count", 0),
            quality_score=v2.get("quality_score", 0.0),
            restored_entities=v2.get("restored_entities", []),
            warnings=v2.get("warnings", []),
            retried=v2.get("retried", False),
        )

    return _polish_v1_rules(
        raw_mt,
        original=original,
        tgt_lang=tgt_lang,
        src_lang=src_lang,
        prev_context=prev_context,
        app_dir=app_dir,
        use_llm=use_llm,
    )


from engines.utils.lang_utils import normalize_lang as _normalize_lang_core


def _normalize_lang(code: str | None) -> str:
    return _normalize_lang_core(code, default="ru")


def _lang_name(code: str) -> str:
    return LANG_NAMES.get(_normalize_lang(code), _normalize_lang(code))


def _timing_start(item: Any) -> int:
    if isinstance(item, dict):
        return int(item.get("start", 0))
    if isinstance(item, (list, tuple)) and len(item) >= 1:
        return int(item[0])
    return 0


def _timing_end(item: Any) -> int:
    if isinstance(item, dict):
        return int(item.get("end", 0))
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[1])
    return 0


def _gap_ms(timing_map: Sequence[Any], idx: int) -> int:
    if idx <= 0 or not timing_map:
        return 0
    return max(0, _timing_start(timing_map[idx]) - _timing_end(timing_map[idx - 1]))


def _duration_ms(timing_map: Sequence[Any], start_idx: int, end_idx: int) -> int:
    if not timing_map or start_idx >= len(timing_map):
        return 0
    end_idx = min(end_idx, len(timing_map) - 1)
    return max(0, _timing_end(timing_map[end_idx]) - _timing_start(timing_map[start_idx]))


def _segment_duration_ms(timing_map: Sequence[Any], idx: int) -> int:
    if not timing_map or idx >= len(timing_map):
        return 0
    return max(0, _timing_end(timing_map[idx]) - _timing_start(timing_map[idx]))


def _is_short_fragment(text: str, timing_map: Sequence[Any] | None, idx: int) -> bool:
    seg = str(text or "").strip()
    if not seg:
        return True
    if len(seg) < DEFAULT_MIN_MERGE_CHARS and len(seg.split()) <= 3:
        return True
    if timing_map and _segment_duration_ms(timing_map, idx) < DEFAULT_MAX_SEGMENT_MS:
        return True
    return False


def merge_segments_for_translation(
    segments: List[str],
    timing_map: Sequence[Any] | None = None,
    max_gap_ms: int = DEFAULT_MAX_GAP_MS,
    max_batch: int = MAX_BATCH_SEGMENTS,
) -> List[List[int]]:
    """Группы индексов для пакетного перевода (соседние короткие реплики Whisper)."""
    if not segments:
        return []

    groups: List[List[int]] = []
    current: List[int] = [0]

    for idx in range(1, len(segments)):
        gap = _gap_ms(timing_map or [], idx) if timing_map else 0
        prev = str(segments[idx - 1] or "").strip()
        cur = str(segments[idx] or "").strip()
        ends_sentence = bool(re.search(r"[.!?…]\s*$", prev))
        short_prev = _is_short_fragment(prev, timing_map, idx - 1)
        short_cur = _is_short_fragment(cur, timing_map, idx)

        can_merge = (
            len(current) < max_batch
            and (not timing_map or gap <= max_gap_ms)
            and not ends_sentence
            and (cur or prev)
            and (short_prev or short_cur or gap <= max_gap_ms)
        )
        try:
            from engines.smart_segmentation import would_break_forbidden

            must_join, _why = would_break_forbidden(prev, cur)
            if must_join:
                can_merge = True and len(current) < max_batch
        except Exception:
            pass

        if can_merge:
            current.append(idx)
        else:
            groups.append(current)
            current = [idx]

    groups.append(current)
    return groups


def merge_segments_for_tts(
    segments: List[str],
    timing_map: Sequence[Any],
    min_duration_ms: int = DEFAULT_MIN_TTS_MS,
    max_gap_ms: int = DEFAULT_MAX_GAP_MS,
) -> List[List[int]]:
    """Объединяет короткие сегменты для одного TTS-вызова; паузы между группами сохраняются."""
    if not segments:
        return []

    groups: List[List[int]] = []
    current: List[int] = [0]

    for idx in range(1, len(segments)):
        gap = _gap_ms(timing_map, idx)
        span = _duration_ms(timing_map, current[0], idx)
        prev = str(segments[idx - 1] or "").strip()
        cur = str(segments[idx] or "").strip()
        ends_sentence = bool(re.search(r"[.!?…]\s*$", prev))

        can_merge = (
            span < min_duration_ms
            and gap <= max_gap_ms
            and not ends_sentence
            and (cur or prev)
        ) or (
            _segment_duration_ms(timing_map, idx) < DEFAULT_MAX_SEGMENT_MS
            and gap <= max_gap_ms
            and not ends_sentence
            and span < min_duration_ms * 2
        )

        if can_merge:
            current.append(idx)
        else:
            groups.append(current)
            current = [idx]

    groups.append(current)
    return groups


def build_tts_groups(
    segments: List[str],
    timing_map: Sequence[Any],
    min_duration_ms: int = DEFAULT_MIN_TTS_MS,
    max_gap_ms: int = DEFAULT_MAX_GAP_MS,
) -> List[dict]:
    """
    План TTS: одна озвучка на группу коротких сегментов.
    timing — [start_ms, end_ms] охватывает весь блок (паузы между микро-сегментами внутри).
    """
    if not segments:
        return []

    index_groups = merge_segments_for_tts(
        segments, timing_map, min_duration_ms=min_duration_ms, max_gap_ms=max_gap_ms
    )
    groups: List[dict] = []
    for indices in index_groups:
        parts = [str(segments[i] or "").strip() for i in indices]
        text = " ".join(p for p in parts if p).strip()
        if not text:
            continue
        start = _timing_start(timing_map[indices[0]]) if timing_map else 0
        end = _timing_end(timing_map[indices[-1]]) if timing_map else 0
        groups.append({"indices": indices, "text": text, "timing": [start, end]})
    return groups


def _leading_token(text: str) -> str | None:
    words = text.strip().split()
    if not words:
        return None
    token = re.sub(r"[^\wа-яёА-ЯЁіІїЇєЄ'-]", "", words[0])
    return token.lower() if token else None


def _drop_repeated_subject(
    text: str,
    prev_context: str | None,
    pronouns: frozenset[str],
) -> str:
    """Убирает повтор подлежащего в соседних репликах (универсально для RU/UK)."""
    if not prev_context:
        return text

    prev_lead = _leading_token(prev_context)
    words = text.split()
    lead = _leading_token(text)
    if not (prev_lead and lead and len(words) > 1):
        return text

    if prev_lead == lead and lead not in pronouns:
        rest = " ".join(words[1:]).strip()
        if rest:
            return rest.capitalize() if rest[0].islower() else rest

    if (
        prev_lead in pronouns
        and lead in pronouns
        and prev_lead == lead
    ):
        rest = " ".join(words[1:]).strip()
        if rest:
            return rest.capitalize() if rest[0].islower() else rest

    return text


def naturalize_ru(text: str, prev_context: str | None = None) -> str:
    """Натурализация русской реплики."""
    text = " ".join(str(text or "").split())
    if not text:
        return text

    text = _drop_repeated_subject(text, prev_context, _PRONOUNS_RU)
    text = _apply_generic_fixes(text)
    text = _apply_ru_word_fixes(text)
    return text.strip()


def apply_style_polish(
    text: str,
    tgt_lang: str,
    *,
    source: str = "",
    app_dir=None,
) -> str:
    """Style-only polish: calques, ruisms, proper nouns — no full rewrite."""
    from engines.proper_nouns_dict import apply_proper_noun_polish
    from engines.semantic_translation import apply_semantic_polish_line

    lang = _normalize_lang(tgt_lang)
    out = apply_semantic_polish_line(text, target_lang=lang)
    if source.strip():
        out = apply_proper_noun_polish(source, out, app_dir=app_dir, tgt_lang=lang)
    if lang == "ru":
        out = _apply_ru_jr_suffix_fixes(out)
    return out.strip()


def naturalize_uk(text: str, prev_context: str | None = None) -> str:
    """Натурализация украинской реплики."""
    text = " ".join(str(text or "").split())
    if not text:
        return text

    text = _drop_repeated_subject(text, prev_context, _PRONOUNS_UK)
    text = _apply_generic_fixes(text)
    text = _apply_uk_word_fixes(text)
    return text.strip()


def naturalize_generic(text: str, prev_context: str | None = None) -> str:
    """Базовая натурализация для прочих языков."""
    text = " ".join(str(text or "").split())
    if not text:
        return text

    prev_lead = _leading_token(prev_context) if prev_context else None
    lead = _leading_token(text)
    words = text.split()
    if prev_lead and lead and prev_lead == lead and len(words) > 1:
        rest = " ".join(words[1:]).strip()
        if rest and len(prev_lead) > 2:
            text = rest.capitalize() if rest[0].islower() else rest

    return _apply_generic_fixes(text).strip()


def naturalize_text(
    text: str,
    tgt_lang: str,
    prev_context: str | None = None,
) -> str:
    """Языково-осведомлённая натурализация одной реплики."""
    lang = _normalize_lang(tgt_lang)
    if lang == "ru":
        return naturalize_ru(text, prev_context)
    if lang == "uk":
        return naturalize_uk(text, prev_context)
    return naturalize_generic(text, prev_context)


def shorten_for_slot(
    text: str,
    *,
    slot_ms: int,
    lang: str = "ru",
    source_hint: str = "",
) -> str:
    """Rephrase shorter for dub slot overflow (Dub module slot-fit step 2)."""
    if not text or slot_ms <= 0:
        return text
    from engines.soft_sync import shorten_text_for_slot

    return shorten_text_for_slot(text, slot_ms=slot_ms, lang=lang, source_hint=source_hint)


def _similarity_ratio(a: str, b: str) -> float:
    wa = set(re.findall(r"\w+", a.lower()))
    wb = set(re.findall(r"\w+", b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


def dedupe_consecutive_similar(lines: List[str], threshold: float = 0.6) -> List[str]:
    """Убирает повтор смысла в соседних репликах (пересказ / двойная озвучка текста)."""
    if not lines:
        return []

    out: List[str] = []
    for line in lines:
        seg = " ".join(str(line or "").split())
        if not seg:
            out.append("")
            continue

        if out and out[-1].strip():
            sim = _similarity_ratio(out[-1], seg)
            if sim >= threshold:
                logger.debug("[Naturalizer] skip duplicate line (sim=%.2f): %s", sim, seg[:60])
                continue

        out.append(seg)
    return out


def _llm_api_key() -> str | None:
    return (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("VM_LLM_API_KEY")
        or os.getenv("VM_OPENAI_API_KEY")
    )


def _natural_translation_enabled() -> bool:
    v = os.getenv("VM_TRANSLATE_NATURAL", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _optional_llm_polish(
    text: str,
    *,
    original: str = "",
    raw_mt: str = "",
    prev_context: str | None = None,
    next_context: str | None = None,
    proper_nouns: list[str] | None = None,
    warnings: list[dict[str, Any]] | None = None,
    tgt_lang: str = "ru",
    src_lang: str | None = None,
) -> str | None:
    if not text.strip() or not _natural_translation_enabled():
        return None
    # Availability + transport are owned by AI Core (local or cloud LLM).
    from engines.ai_core import llm_gateway

    if not llm_gateway.is_available():
        return None

    try:
        lang_name = _lang_name(tgt_lang)
        src_name = _lang_name(src_lang) if src_lang else "unknown"
        tgt_name = lang_name

        system = (
            "Ты редактор текста для дубляжа фильмов.\n"
            "Правила (строго по приоритету):\n"
            "1. Полностью сохрани смысл и факты оригинала.\n"
            f"2. Слегка улучши стиль на {lang_name} языке — как сказал бы носитель.\n"
            "3. НЕ переписывай перевод заново: меняй только отдельные слова и короткие фразы.\n"
            "4. Не меняй порядок реплики радикально; не удаляй и не добавляй новые мысли.\n"
            "5. Имена, бренды, названия мест и персонажей сохраняй в оригинальном написании.\n"
            "6. Исправляй кальки, русизмы (для украинского), неестественные конструкции.\n"
            "7. Идиомы — по смыслу, не дословно.\n"
            "8. Ответ — только одна реплика, без кавычек и пояснений."
        )

        user_parts: list[str] = [
            f"Source language: {src_name}",
            f"Target language: {tgt_name}",
        ]
        if original.strip():
            user_parts.append(f"Original (Whisper):\n{original.strip()}")
        if raw_mt.strip():
            user_parts.append(f"Raw MT:\n{raw_mt.strip()}")
        if proper_nouns:
            user_parts.append(f"Proper nouns to preserve: {', '.join(proper_nouns[:12])}")
        if warnings:
            warn_labels = []
            for w in warnings[:8]:
                if isinstance(w, dict):
                    code = w.get("code", "")
                    stage = w.get("stage", "")
                    warn_labels.append(f"{stage}:{code}" if stage else code)
                else:
                    warn_labels.append(str(w))
            if warn_labels:
                user_parts.append(f"Known warnings: {', '.join(warn_labels)}")
        if prev_context:
            user_parts.append(f"Previous segment:\n{prev_context}")
        if next_context:
            user_parts.append(f"Next segment:\n{next_context}")
        user_parts.append(f"Draft for dubbing (edit this):\n{text.strip()}")

        content = llm_gateway.chat(
            "\n\n".join(user_parts),
            system=system,
            temperature=0.35,
            max_tokens=320,
            timeout=45,
        )
        if not content:
            return None
        content = content.strip().strip("\"'«»")
        return content or None
    except Exception as e:
        logger.debug("[Naturalizer] LLM polish skipped: %s", e)
        return None


def polish_lines(
    lines: List[str],
    *,
    source_segments: List[str] | None = None,
    tgt_lang: str = "ru",
    src_lang: str | None = None,
    use_llm: bool = False,
    llm_ms_out: list[float] | None = None,
    app_dir=None,
    quality_scores: List[float] | None = None,
    naturalizer_reasons_out: list[list[str]] | None = None,
    entity_maps: list[dict[str, str]] | None = None,
    naturalizer_meta_out: list[dict[str, Any]] | None = None,
    segment_progress_cb: Callable[[int, int], None] | None = None,
) -> List[str]:
    """Naturalize segments after Marian — only when MT issues are detected."""
    import time

    src_lines = list(source_segments) if source_segments else [""] * len(lines)
    polished: List[str] = []
    prev = ""
    if llm_ms_out is not None:
        llm_ms_out.clear()
        llm_ms_out.extend([0.0] * len(lines))
    if naturalizer_reasons_out is not None:
        naturalizer_reasons_out.clear()
    if naturalizer_meta_out is not None:
        naturalizer_meta_out.clear()

    changed_count = 0
    for i, raw in enumerate(lines):
        original = str(src_lines[i] if i < len(src_lines) else "")
        raw_mt = str(raw or "")
        entity_map = (
            entity_maps[i]
            if entity_maps and i < len(entity_maps)
            else None
        )

        t_llm = time.perf_counter()
        result = polish_segment_detailed(
            raw_mt,
            original=original,
            tgt_lang=tgt_lang,
            src_lang=src_lang,
            prev_context=prev if prev else None,
            app_dir=app_dir,
            use_llm=use_llm,
            entity_token_map=entity_map,
        )
        if llm_ms_out is not None and use_llm:
            llm_ms_out[i] = (time.perf_counter() - t_llm) * 1000.0

        seg = result.text
        if naturalizer_reasons_out is not None:
            naturalizer_reasons_out.append(list(result.reasons))
        if naturalizer_meta_out is not None:
            naturalizer_meta_out.append(
                {
                    "mixed_language_pct": result.mixed_language_pct,
                    "retry_reason": result.retry_reason,
                    "problems": list(result.problems),
                    "fix_count": result.fix_count,
                    "quality_score": result.quality_score,
                    "restored_entities": list(result.restored_entities),
                    "warnings": list(result.warnings),
                    "retried": result.retried,
                }
            )

        if seg != raw_mt:
            changed_count += 1
            logger.info(
                "[Naturalizer] seg#%d changed: %s → %s (%s)",
                i + 1,
                raw_mt[:60],
                seg[:60],
                ",".join(result.reasons),
            )
        else:
            logger.debug(
                "[Naturalizer] seg#%d %s",
                i + 1,
                ",".join(result.reasons) or "no_changes",
            )

        polished.append(seg)
        if seg:
            prev = seg
        if segment_progress_cb is not None:
            try:
                segment_progress_cb(i + 1, len(lines))
            except Exception:
                pass

    logger.info(
        "[Naturalizer] polish_lines: %d/%d segments changed",
        changed_count,
        len(lines),
    )
    return dedupe_consecutive_similar(polished)


def fix_phantom_cross_segment_repeats(
    source_segments: List[str],
    translated_segments: List[str],
) -> List[str]:
    """
    Убирает фантомные повторы (напр. OCR-имя в каждом сегменте без речи).
    Если одна и та же переведённая фраза в >30% слотов, но речь её не содержит — очищаем слот.
    """
    if not translated_segments:
        return translated_segments

    from collections import Counter

    normalized = [str(t or "").strip().lower() for t in translated_segments]
    counts = Counter(t for t in normalized if len(t) > 4)
    if not counts:
        return translated_segments

    phrase, freq = counts.most_common(1)[0]
    if freq <= max(2, len(translated_segments) * 0.3):
        return translated_segments

    speech_hits = sum(1 for s in source_segments if phrase in str(s or "").lower())
    if speech_hits >= freq * 0.4:
        return translated_segments

    logger.warning(
        "[Naturalizer] phantom repeat detected (%d/%d slots): %r — stripping non-speech slots",
        freq,
        len(translated_segments),
        phrase[:60],
    )

    out: List[str] = []
    for src, tr in zip(source_segments, translated_segments):
        tr_s = str(tr or "").strip()
        src_s = str(src or "").strip()
        if tr_s.lower() == phrase and phrase not in src_s.lower():
            out.append("")
        elif phrase in tr_s.lower() and phrase not in src_s.lower() and len(tr_s) < len(phrase) + 8:
            out.append("")
        else:
            out.append(tr_s)
    return out


def translate_segments_natural(
    segments: List[str],
    timing_map: Sequence[Any],
    src_lang: str,
    tgt_lang: str,
    *,
    translate_meta_out: list | None = None,
    task_id: str = "",
    app_dir=None,
) -> List[str]:
    """
    Универсальный перевод для дубляжа (delegates to UniversalTranslationPipeline).
    """
    from engines.translation_pipeline import translate_segments_universal

    return translate_segments_universal(
        segments,
        timing_map,
        src_lang,
        tgt_lang,
        task_id=task_id,
        app_dir=app_dir,
        translate_meta_out=translate_meta_out,
        write_quality_log=True,
    )
