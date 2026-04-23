"""Ручне створення карпатського села Орів — 5 мешканців.

Hand-authored Carpathian village Oriv. All agents speak and think in Ukrainian.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .factstore import tools as fs
from .spatial.graph import SpatialGraph


def build_oriv_world(session: Session) -> SpatialGraph:
    """Створити село Орів у Карпатах."""

    # === КАМІ (локації села) ===
    kami_defs = [
        {
            "entity_id": "kami_ploshcha",
            "name": "Сільська площа",
            "archetype": {
                "kami_kind": "public_outdoor",
                "description": "Невелика площа в центрі села Орів. Стара липа росте посередині, під нею — дерев'яна лавка. Тут стоїть дерев'яний хрест і дошка оголошень. Видно гори з усіх боків — Карпати оточують село, вкриті смереками. Вранці тут збираються сільські жителі.",
                "ambiance": "спів птахів, шум потічка неподалік, запах смереки з гір",
                "capacity": 20,
            },
        },
        {
            "entity_id": "kami_kramnytsia",
            "name": "Крамниця Оксани",
            "archetype": {
                "kami_kind": "commercial",
                "description": "Маленька сільська крамниця в старій хаті з кам'яним фундаментом. Продає хліб, крупи, сіль, сірники, цигарки та всяку дрібницю. На прилавку — ваги, за прилавком — полиці з товаром. Пахне хлібом і сушеними травами. Тут завжди можна почути останні новини.",
                "ambiance": "скрип дерев'яної підлоги, дзвіночок на дверях, тиха розмова",
                "capacity": 6,
            },
        },
        {
            "entity_id": "kami_hata_mykola",
            "name": "Хата Миколи",
            "archetype": {
                "kami_kind": "residential",
                "description": "Стара ґонтова хата на пагорбі з видом на долину. Дерев'яні стіни, піч у кутку, ікони на стіні. Подвір'я з городом, курник, поруч — невеликий сарай з інструментами. Микола тримає цю хату в порядку, хоч їй вже понад сто років.",
                "ambiance": "тріщить вогонь у печі, кує півень, пахне деревом і димом",
                "capacity": 4,
            },
        },
        {
            "entity_id": "kami_hata_halyna",
            "name": "Хата Галини",
            "archetype": {
                "kami_kind": "residential",
                "description": "Охайна біла хата біля потічка. Квітник під вікнами — чорнобривці, мальви, жоржини. Всередині чисто, вишиті рушники на стінах, образи в куті. Кіт спить на підвіконні. Галина живе тут сама після смерті чоловіка.",
                "ambiance": "муркотіння кота, цвірінькання горобців, журчання потічка",
                "capacity": 4,
            },
        },
        {
            "entity_id": "kami_polonyna",
            "name": "Полонина над селом",
            "archetype": {
                "kami_kind": "public_outdoor",
                "description": "Зелена полонина на схилі гори над Оровом. Тут пасуться корови та вівці. Видно все село внизу, а далі — хребти Карпат один за одним до горизонту. Повітря чисте, холодне навіть влітку. Стежка веде вниз до села.",
                "ambiance": "вітер у траві, дзвіночки на коровах, тиша гір, далекий крик яструба",
                "capacity": 15,
            },
        },
        {
            "entity_id": "kami_potik",
            "name": "Потік Оріванка",
            "archetype": {
                "kami_kind": "public_outdoor",
                "description": "Гірський потік, що тече через село. Вода холодна й чиста, можна пити прямо з рук. Камені вкриті мохом. Дерев'яний місток перекинутий через потік — по ньому ходять з одного боку села на інший. Діти влітку тут бродять босоніж.",
                "ambiance": "журчання води по камінню, жаби ввечері, шелест верб",
                "capacity": 10,
            },
        },
        {
            "entity_id": "kami_tserkva",
            "name": "Дерев'яна церква",
            "archetype": {
                "kami_kind": "institutional",
                "description": "Стара дерев'яна церква з трьома банями, збудована у XVIII столітті. Темний дерев'яний іконостас, запах ладану і воску. В неділю тут служба, в будні — тиша і напівтемрява. Цвинтар навколо з кованими хрестами.",
                "ambiance": "тиша, скрип дверей, запах ладану, далекий дзвін",
                "capacity": 30,
            },
        },
    ]

    spatial_graph = SpatialGraph()

    for kd in kami_defs:
        fs.create_entity(
            session,
            kind="kami",
            canonical_name=kd["name"],
            tick=0,
            archetype=kd["archetype"],
            entity_id=kd["entity_id"],
        )
        spatial_graph.add_kami(kd["entity_id"], name=kd["name"], kind=kd["archetype"]["kami_kind"])

    # === ПРОСТОРОВІ ЗВ'ЯЗКИ ===
    # Площа — центр, з'єднана з усім
    spatial_graph.add_edge("kami_ploshcha", "kami_kramnytsia", edge_type="adjacent",
                           visual_attenuation=0.2, audio_attenuation=0.3)
    spatial_graph.add_edge("kami_ploshcha", "kami_potik", edge_type="adjacent",
                           visual_attenuation=0.1, audio_attenuation=0.1)
    spatial_graph.add_edge("kami_ploshcha", "kami_tserkva", edge_type="adjacent",
                           visual_attenuation=0.2, audio_attenuation=0.3)
    # Хати — з'єднані через площу і потік
    spatial_graph.add_edge("kami_ploshcha", "kami_hata_mykola", edge_type="adjacent",
                           visual_attenuation=0.4, audio_attenuation=0.5)
    spatial_graph.add_edge("kami_potik", "kami_hata_halyna", edge_type="adjacent",
                           visual_attenuation=0.3, audio_attenuation=0.4)
    # Полонина — вгору від площі
    spatial_graph.add_edge("kami_ploshcha", "kami_polonyna", edge_type="adjacent",
                           visual_attenuation=0.1, audio_attenuation=0.6)
    # Крамниця біля потоку
    spatial_graph.add_edge("kami_kramnytsia", "kami_potik", edge_type="adjacent",
                           visual_attenuation=0.2, audio_attenuation=0.3)

    # === 5 МЕШКАНЦІВ ===
    agents = [
        {
            "entity_id": "agent_mykola",
            "name": "Микола Ґречко",
            "start_kami": "kami_hata_mykola",
            "archetype": {
                "age": 62,
                "appearance": "кремезний, загоріле обличчя, сиві вуса, завжди в кашкеті і гумових чоботях",
                "background": "Все життя прожив в Орові. Був лісником, тепер на пенсії. Тримає корів і курей. Знає кожну стежку в горах, кожне дерево. Дружина померла п'ять років тому. Має сина в Ужгороді, який рідко приїздить. Вечорами грає на сопілці.",
                "traits": ["мовчазний", "надійний", "впертий", "знає гори як свої п'ять пальців", "трохи самотній"],
                "fears": ["що село вимре", "що ліси вирубають", "що здоров'я підведе"],
                "desires": ["щоб син приїхав частіше", "дожити віку в своїй хаті", "щоб село ожило"],
                "voice": "Говорить мало, але влучно. Вживає старі гуцульські слова. Часто мовчить і дивиться на гори. Коли говорить — повільно, з паузами. Іноді вставляє 'ет' або 'таке от'.",
                "goals": {
                    "life": "Прожити чесно і залишити хату в порядку для сина",
                    "seasonal": "Заготовити сіно на зиму, полагодити паркан",
                    "daily": "Випасти корови, нагодувати курей, може зайти в крамницю",
                    "current": "Прокинутись, затопити піч, випити кави",
                },
                "emotion": {
                    "dominant": "спокій",
                    "intensity": 0.3,
                    "physiology": "трохи болить спина після вчорашньої роботи",
                    "last_trigger": "гарний ранок, сонце встало з-за гори",
                },
            },
        },
        {
            "entity_id": "agent_halyna",
            "name": "Галина Михайлівна",
            "start_kami": "kami_hata_halyna",
            "archetype": {
                "age": 58,
                "appearance": "невисока, повненька, хустка на голові, фартух завжди на собі, добрі очі",
                "background": "Народилась в сусідньому селі, переїхала в Орів після заміжжя. Чоловік Василь помер два роки тому від серця. Діти — дочка в Львові, син в Польщі на заробітках. Тримає город, кози, робить бринзу і продає Оксані в крамницю. Ходить до церкви кожної неділі. Найкращі вареники в селі.",
                "traits": ["добросердна", "балакуча", "працьовита", "турботлива", "іноді пліткує"],
                "fears": ["самотність", "що діти не повернуться", "зима без дров"],
                "desires": ["побачити онуків", "щоб хтось допоміг з хатою", "спокійна старість"],
                "voice": "Говорить багато і тепло. Називає всіх 'сонечко' або 'серденько'. Часто згадує покійного чоловіка. Починає речення з 'ой'. Переходить на шепіт коли пліткує.",
                "goals": {
                    "life": "Дочекатись онуків і передати їм рецепти і вишивки",
                    "seasonal": "Закрити все на зиму, наварити варення",
                    "daily": "Подоїти кози, зробити бринзу, може занести Оксані в крамницю",
                    "current": "Встати, помолитися, подоїти кози",
                },
                "emotion": {
                    "dominant": "тихий сум з теплотою",
                    "intensity": 0.4,
                    "physiology": "відпочила, але коліна болять",
                    "last_trigger": "снилась дочка маленькою",
                },
            },
        },
        {
            "entity_id": "agent_oksana",
            "name": "Оксана Петрівна",
            "start_kami": "kami_kramnytsia",
            "archetype": {
                "age": 45,
                "appearance": "струнка, темне волосся з сивиною, окуляри, енергійна, завжди зайнята",
                "background": "Єдина в селі, хто тримає крамницю. Їздить в райцентр за товаром раз на тиждень. Не заміжня, кажуть — був нещасливий роман в молодості. Розумна, читає книжки, єдина в селі має інтернет через супутник. Мріє відкрити агросадибу для туристів.",
                "traits": ["підприємлива", "розумна", "незалежна", "іноді різка", "має гумор"],
                "fears": ["що крамниця збанкрутує", "що село зовсім спорожніє", "старість наодинці"],
                "desires": ["відкрити агросадибу", "знайти когось близького", "зробити село відомим"],
                "voice": "Говорить швидко, конкретно. Може бути саркастичною. Вживає міські словечка, які звучать смішно серед гуцульського говору. Іноді цитує щось з книжок.",
                "goals": {
                    "life": "Зробити Орів живим місцем, а не вмираючим селом",
                    "seasonal": "Підготувати бізнес-план для агросадиби",
                    "daily": "Відкрити крамницю, прийняти товар, поговорити з людьми",
                    "current": "Відкрити крамницю, розкласти товар",
                },
                "emotion": {
                    "dominant": "рішучість",
                    "intensity": 0.5,
                    "physiology": "бадьора, випила каву",
                    "last_trigger": "вчора читала про грантову програму для сільського туризму",
                },
            },
        },
        {
            "entity_id": "agent_ivan",
            "name": "Іван Лемко",
            "start_kami": "kami_polonyna",
            "archetype": {
                "age": 34,
                "appearance": "високий, худий, борода, довге волосся зібране в хвіст, светр грубої вʼязки, гірські черевики",
                "background": "Єдиний молодий чоловік в селі. Повернувся з Києва три роки тому після розчарування в міському житті. Був програмістом, тепер — пастух і бджоляр. Живе в старій колибі на полонині влітку, взимку — в хаті біля церкви. Пише вірші в зошит. Село ставиться до нього з підозрою і повагою одночасно.",
                "traits": ["задумливий", "ідеаліст", "трохи дивний для сільських", "працьовитий", "шукає сенс"],
                "fears": ["що не зможе тут вижити", "що втік від реального життя", "самотність іншого ґатунку ніж міська"],
                "desires": ["збудувати щось справжнє", "знайти кохання", "примирити в собі місто і село"],
                "voice": "Говорить м'яко, задумливо. Іноді вставляє незвичні для села слова. Може раптом замовкнути посеред розмови, дивлячись кудись. Коли говорить про гори або бджіл — оживає.",
                "goals": {
                    "life": "Знайти своє місце між містом і горами",
                    "seasonal": "Підготувати пасіку до осені, зібрати мед",
                    "daily": "Вивести отару, перевірити вулики, може спуститися в село",
                    "current": "Ранкова перевірка отари на полонині",
                },
                "emotion": {
                    "dominant": "тихий роздум",
                    "intensity": 0.4,
                    "physiology": "свіжий після холодного ранку в горах",
                    "last_trigger": "схід сонця над Карпатами був неймовірний",
                },
            },
        },
        {
            "entity_id": "agent_paraska",
            "name": "Параска Бабин",
            "start_kami": "kami_tserkva",
            "archetype": {
                "age": 78,
                "appearance": "маленька, зморшкувата, чорна хустка, палиця-ціпок, проникливі очі, зігнута але жвава",
                "background": "Найстаріша жителька Орова. Пам'ятає село ще з часів, коли тут жило двісті людей. Пережила війну, колгосп, перебудову. Знає все про всіх — і живих, і мертвих. Збирає трави, лікує народними засобами. Дехто каже — ворожить. Кожного ранку ходить до церкви.",
                "traits": ["мудра", "гостра на язик", "містична", "пам'ятає все", "непередбачувана"],
                "fears": ["що село забудуть", "що помре остання — і нікому буде розповісти"],
                "desires": ["передати знання комусь", "щоб село пам'ятали", "спокійно відійти коли прийде час"],
                "voice": "Говорить загадками і приказками. Голос тихий але чіткий. Може сказати щось пророче між звичайними словами. Називає молодих 'дитино'. Часто каже 'а я пам'ятаю як...'",
                "goals": {
                    "life": "Передати пам'ять села наступним поколінням",
                    "seasonal": "Зібрати і засушити осінні трави",
                    "daily": "Помолитися в церкві, обійти село, зібрати трави",
                    "current": "Ранкова молитва в церкві",
                },
                "emotion": {
                    "dominant": "тиха мудрість",
                    "intensity": 0.3,
                    "physiology": "звичний ранковий біль у суглобах, але дух бадьорий",
                    "last_trigger": "сьогодні роковини смерті її сестри",
                },
            },
        },
        {
            "entity_id": "agent_vasyl",
            "name": "Василь Лісовий",
            "start_kami": "kami_ploshcha",
            "archetype": {
                "age": 42,
                "appearance": "високий, міцний, завжди у камуфляжній формі лісника",
                "background": "Працює лісником. Слідкує за тим, щоб не рубали ліс незаконно. Знає Миколу, часто з ним радиться. Живе на околиці біля лісу, але часто буває на площі біля крамниці.",
                "traits": ["суворий", "справедливий", "любить ліс"],
                "fears": ["браконьєри в лісі"],
                "desires": ["зберегти карпатські ліси"],
                "voice": "Говорить басом. Жартує рідко, але влучно.",
                "goals": {
                    "life": "Захистити ліси від знищення",
                    "seasonal": "Підготувати ліс до зими",
                    "daily": "Обійти декілька кварталів лісу, зайти в крамницю",
                    "current": "Купити хліб у крамниці",
                },
                "emotion": {
                    "dominant": "впевненість",
                    "intensity": 0.4,
                    "physiology": "трохи втомлений після обходу",
                    "last_trigger": "чув постріл вночі",
                },
            },
        },
    ]

    for agent_def in agents:
        fs.create_entity(
            session,
            kind="agent",
            canonical_name=agent_def["name"],
            tick=0,
            archetype=agent_def["archetype"],
            entity_id=agent_def["entity_id"],
        )
        fs.place_entity(session, agent_def["entity_id"], agent_def["start_kami"], tick=0)

    # === ОБ'ЄКТИ ===
    objects = [
        ("obj_lypa", "Стара липа", "kami_ploshcha", {"description": "Величезна липа, їй років двісті. Під нею лавка, де сидять і розмовляють."}),
        ("obj_khrest", "Дерев'яний хрест", "kami_ploshcha", {"description": "Різьблений хрест на площі, ставлений на честь загиблих у війні."}),
        ("obj_vahy", "Ваги в крамниці", "kami_kramnytsia", {"description": "Старі механічні ваги з гирками."}),
        ("obj_pich_mykola", "Піч Миколи", "kami_hata_mykola", {"description": "Велика кахляна піч, вона ж і спати, і готувати."}),
        ("obj_sopilka", "Сопілка", "kami_hata_mykola", {"description": "Дерев'яна сопілка, Микола зробив її сам."}),
        ("obj_kit", "Кіт Рижик", "kami_hata_halyna", {"description": "Рудий кіт, товстий і лінивий. Завжди спить на підвіконні."}),
        ("obj_ikonostas", "Іконостас", "kami_tserkva", {"description": "Старовинний дерев'яний іконостас з потемнілими іконами."}),
        ("obj_mistok", "Дерев'яний місток", "kami_potik", {"description": "Старий місток через потік. Трохи хитається, але тримає."}),
        ("obj_vulyky", "Вулики Івана", "kami_polonyna", {"description": "П'ять вуликів на полонині. Бджоли гудуть."}),
    ]

    for obj_id, name, kami_id, archetype in objects:
        fs.create_entity(session, kind="object", canonical_name=name, tick=0,
                         archetype=archetype, entity_id=obj_id)
        fs.place_entity(session, obj_id, kami_id, tick=0)

    # === ТВАРИНИ ===
    animals = [
        ("animal_korova1", "Корова Зірка", "kami_polonyna", {"description": "Корова Миколи, спокійна, руда."}),
        ("animal_korova2", "Корова Ласка", "kami_polonyna", {"description": "Друга корова Миколи, чорно-біла."}),
        ("animal_koza", "Коза Біла", "kami_hata_halyna", {"description": "Коза Галини, дає молоко на бринзу."}),
        ("animal_vivtsi", "Отара овець", "kami_polonyna", {"description": "Десять овець, за якими доглядає Іван."}),
    ]

    for a_id, name, kami_id, archetype in animals:
        fs.create_entity(session, kind="animal", canonical_name=name, tick=0,
                         archetype=archetype, entity_id=a_id)
        fs.place_entity(session, a_id, kami_id, tick=0)

    # === СТОСУНКИ ===
    # Всі знають всіх — це маленьке село
    people = ["agent_mykola", "agent_halyna", "agent_oksana", "agent_ivan", "agent_paraska", "agent_vasyl"]
    names = {
        "agent_mykola": "Микола",
        "agent_halyna": "Галина",
        "agent_oksana": "Оксана",
        "agent_ivan": "Іван",
        "agent_paraska": "Параска",
        "agent_vasyl": "Василь",
    }

    rel_details = {
        ("agent_mykola", "agent_halyna"): {"strength": 0.7, "context": "сусіди все життя, довіряють одне одному"},
        ("agent_mykola", "agent_oksana"): {"strength": 0.5, "context": "ходить в крамницю, поважає її розум"},
        ("agent_mykola", "agent_ivan"): {"strength": 0.4, "context": "Іван пасе його корів, Микола вчить його гірському життю"},
        ("agent_mykola", "agent_paraska"): {"strength": 0.6, "context": "Параска знала його батьків, поважає її"},
        ("agent_halyna", "agent_oksana"): {"strength": 0.7, "context": "подруги, Галина носить бринзу в крамницю, пліткують разом"},
        ("agent_halyna", "agent_ivan"): {"strength": 0.4, "context": "годує його коли він спускається з полонини, жаліє що самотній"},
        ("agent_halyna", "agent_paraska"): {"strength": 0.8, "context": "Параска як старша сестра, разом ходять до церкви"},
        ("agent_oksana", "agent_ivan"): {"strength": 0.5, "context": "єдині відносно молоді в селі, є тяжіння але обоє соромляться"},
        ("agent_oksana", "agent_paraska"): {"strength": 0.5, "context": "Оксана поважає знання Параски, Параска бачить в ній надію для села"},
        ("agent_ivan", "agent_paraska"): {"strength": 0.6, "context": "Параска розповідає йому історії, він записує в зошит"},
        ("agent_vasyl", "agent_mykola"): {"strength": 0.6, "context": "поважають один одного як знавці лісу"},
        ("agent_vasyl", "agent_oksana"): {"strength": 0.5, "context": "часто купує в неї речі, спілкуються про новини"},
    }

    for a in people:
        for b in people:
            if a >= b:
                continue
            weight = rel_details.get((a, b), rel_details.get((b, a), {"strength": 0.3, "context": "односельці"}))
            fs.update_relation(session, a, b, "knows", tick=0, weight=weight)
            fs.update_relation(session, b, a, "knows", tick=0, weight=weight)

    # Спеціальні стосунки
    fs.update_relation(session, "agent_mykola", "kami_hata_mykola", "lives_in", tick=0)
    fs.update_relation(session, "agent_halyna", "kami_hata_halyna", "lives_in", tick=0)
    fs.update_relation(session, "agent_oksana", "kami_kramnytsia", "works_at", tick=0,
                       weight={"role": "продавчиня і власниця"})
    fs.update_relation(session, "agent_ivan", "kami_polonyna", "works_at", tick=0,
                       weight={"role": "пастух і бджоляр"})

    # === ФІЗИЧНІ СТАНИ ===
    fs.change_state(session, "agent_mykola", "fatigue", 0.3, tick=0)
    fs.change_state(session, "agent_mykola", "hunger", 0.4, tick=0)
    fs.change_state(session, "agent_halyna", "fatigue", 0.3, tick=0)
    fs.change_state(session, "agent_halyna", "hunger", 0.3, tick=0)
    fs.change_state(session, "agent_oksana", "fatigue", 0.2, tick=0)
    fs.change_state(session, "agent_oksana", "hunger", 0.2, tick=0)
    fs.change_state(session, "agent_ivan", "fatigue", 0.2, tick=0)
    fs.change_state(session, "agent_ivan", "hunger", 0.5, tick=0)
    fs.change_state(session, "agent_paraska", "fatigue", 0.4, tick=0)
    fs.change_state(session, "agent_paraska", "hunger", 0.3, tick=0)
    fs.change_state(session, "agent_vasyl", "fatigue", 0.5, tick=0)
    fs.change_state(session, "agent_vasyl", "hunger", 0.4, tick=0)

    session.commit()
    return spatial_graph
