/**
 * Northern Sami (Davvisámegiella) words for switch names.
 * Themed around home, nature, light, connection — IKEA-proof and Nordic-sounding.
 *
 * These names are allocated globally by the server to ensure uniqueness.
 * With 200+ words, we can support thousands of switches before needing suffixes.
 */

const SWITCH_NAME_WORDS = [
	// ── Light & Sky ──────────────────────────────────────────────────────────
	'Čuovga',    // light
	'Beaivi',    // sun/day
	'Mánnu',     // moon
	'Albmi',     // sky/heaven
	'Nástit',    // stars
	'Guovssahas', // northern lights
	'Šearri',    // clear sky
	'Idja',      // night
	'Iđit',      // morning
	'Eahket',    // evening

	// ── Nature & Landscape ───────────────────────────────────────────────────
	'Muorra',    // tree/wood
	'Čáhci',     // water
	'Vuovdi',    // forest
	'Johka',     // river
	'Várri',     // mountain
	'Suolu',     // island
	'Jávri',     // lake
	'Mearra',    // sea
	'Luokta',    // bay
	'Njárga',    // peninsula
	'Duottar',   // tundra
	'Jeaggi',    // marsh/bog
	'Geađgi',    // stone/rock
	'Sávza',     // waterfall
	'Gáldu',     // spring (water source)
	'Ávži',      // canyon
	'Čohkka',    // peak/summit
	'Duolba',    // plateau
	'Roavvi',    // forest clearing
	'Suoidni',   // grass/meadow

	// ── Weather & Elements ───────────────────────────────────────────────────
	'Biegga',    // wind
	'Dálki',     // weather
	'Dulvi',     // flood/surge
	'Muohta',    // snow
	'Arvi',      // rain
	'Čiekŋa',    // fog/mist
	'Balva',     // cloud
	'Čuoika',    // frost
	'Dolla',     // fire
	'Šiellu',    // flame
	'Guksi',     // smoke
	'Čáhppat',   // darkness

	// ── Seasons & Time ───────────────────────────────────────────────────────
	'Geassi',    // summer
	'Dálvi',     // winter
	'Čakča',     // autumn
	'Giđđa',     // spring
	'Vahkku',    // week
	'Jándor',    // new year
	'Skábma',    // polar night

	// ── Animals ──────────────────────────────────────────────────────────────
	'Guolli',    // fish
	'Loddi',     // bird
	'Boazu',     // reindeer
	'Guovža',    // bear
	'Gumppe',    // wolf
	'Rieban',    // fox
	'Ealga',     // elk/moose
	'Goddi',     // wild reindeer
	'Njálla',    // arctic fox
	'Spiidni',   // spider
	'Vuojaš',    // otter
	'Njiŋŋelas', // weasel
	'Oarri',     // squirrel
	'Sáhpán',    // mouse
	'Rávdu',     // trout
	'Luossa',    // salmon
	'Dápmot',    // char (fish)
	'Čuonjá',    // goose
	'Njukča',    // swan
	'Bulddogas', // ptarmigan
	'Goaskin',   // eagle
	'Skuolfi',   // raven
	'Cizáš',     // small bird
	'Sáhppi',    // hare
	'Goadjin',   // wolverine
	'Albas',     // ermine

	// ── Plants ───────────────────────────────────────────────────────────────
	'Muorji',    // berry
	'Jokŋa',     // cloudberry
	'Sarat',     // lingonberry
	'Čáhppesmuorji', // crowberry
	'Rássi',     // grass
	'Jeagil',    // heather
	'Sieđga',    // willow
	'Beassi',    // birch
	'Guossa',    // spruce
	'Bievla',    // leaf

	// ── People & Family ──────────────────────────────────────────────────────
	'Nieida',    // daughter/girl
	'Gánda',     // son/boy
	'Áhkku',     // grandmother
	'Áddjá',     // grandfather
	'Eadni',     // mother
	'Áhčči',     // father
	'Oabbá',     // sister
	'Viellja',   // brother
	'Mánná',     // child
	'Olmmái',    // man
	'Nissun',    // woman
	'Ustit',     // friend
	'Ráhkis',    // beloved
	'Guossi',    // guest

	// ── Culture & Tradition ──────────────────────────────────────────────────
	'Goahti',    // traditional dwelling
	'Lavvu',     // tent
	'Gákti',     // traditional dress
	'Duodji',    // handicraft
	'Joiku',     // traditional song
	'Sieidi',    // sacred place
	'Siida',     // community
	'Sápmi',     // Sami homeland
	'Noaidi',    // shaman
	'Runebomme', // drum
	'Gietkka',   // cradle
	'Lávka',     // cloth/fabric
	'Náhkki',    // leather
	'Bealji',    // ear (earring)
	'Vuodda',    // belt

	// ── Tools & Objects ──────────────────────────────────────────────────────
	'Njuolla',   // arrow
	'Dávgi',     // bow
	'Niibi',     // knife
	'Guksi',     // wooden cup
	'Giisá',     // chest/box
	'Ráidu',     // sled caravan
	'Geares',    // sled
	'Suohpan',   // lasso
	'Boahkán',   // staff
	'Áiru',      // oar
	'Fanas',     // boat
	'Vuoddji',   // rope
	'Gárri',     // fence
	'Áiddi',     // enclosure

	// ── Actions & Concepts ───────────────────────────────────────────────────
	'Sáhka',     // speech/talk
	'Vuohki',    // way/path
	'Báiki',     // place
	'Giella',    // language
	'Gierdu',    // patience
	'Oaidnu',    // sight/view
	'Ruoktu',    // home
	'Doaibma',   // action/work
	'Jurdda',    // thought
	'Dovdu',     // feeling
	'Vuoiŋŋa',   // spirit/breath
	'Fápmu',     // power/strength
	'Dáidu',     // skill
	'Máhttu',    // knowledge
	'Vierru',    // habit/custom
	'Árvvu',     // honour/worth
	'Ráfi',      // peace
	'Illu',      // joy
	'Moraš',     // sorrow
	'Ballu',     // fear
	'Doaivu',    // hope
	'Luohttámuš', // trust

	// ── Mythology & Spirits ──────────────────────────────────────────────────
	'Stállu',    // mythical giant
	'Gonagas',   // king
	'Hearrá',    // lord/master
	'Eallu',     // herd
	'Čuđit',     // enemy (mythical)
	'Ulda',      // underground people
	'Mánáidahkku', // child spirit
	'Sáivu',     // sacred lake
	'Jabmi',     // realm of dead

	// ── Numbers & Directions ─────────────────────────────────────────────────
	'Okta',      // one
	'Guokte',    // two
	'Golbma',    // three
	'Njeallje',  // four
	'Vihtta',    // five
	'Guhtta',    // six
	'Čieža',     // seven
	'Gávcci',    // eight
	'Ovcci',     // nine
	'Logi',      // ten
	'Davvi',     // north
	'Lulli',     // south
	'Nuorti',    // east
	'Oarji',     // west

	// ── Colours ──────────────────────────────────────────────────────────────
	'Vilges',    // white
	'Čáhppat',   // black
	'Ruoksat',   // red
	'Fiskat',    // yellow
	'Alit',      // high/tall (also blue-ish)
	'Ruoná',     // green
	'Alitvuohta', // brightness

	// ── More Nature ──────────────────────────────────────────────────────────
	'Gierdu',    // circle/round
	'Čuolda',    // throat/narrow pass
	'Gáddi',     // shore/bank
	'Njálbmi',   // mouth (of river)
	'Čoarvi',    // horn/antler
	'Bálggis',   // path/trail
	'Ráigi',     // hole/opening
	'Luodda',    // track/trace
	'Gohpi',     // hollow/valley
	'Oaivi',     // head/hilltop
];

const SWITCH_NAME_PREFIX = 'VomeSync';

function getWordList() {
	return SWITCH_NAME_WORDS.slice();
}

function getWordCount() {
	return SWITCH_NAME_WORDS.length;
}

function getWordAtIndex(index) {
	if (index < 0 || index >= SWITCH_NAME_WORDS.length) {
		return null;
	}
	return SWITCH_NAME_WORDS[index];
}

function formatSwitchName(word, suffix = null) {
	if (suffix !== null && suffix > 0) {
		return `${SWITCH_NAME_PREFIX} ${word} ${suffix}`;
	}
	return `${SWITCH_NAME_PREFIX} ${word}`;
}

module.exports = {
	SWITCH_NAME_WORDS,
	SWITCH_NAME_PREFIX,
	getWordList,
	getWordCount,
	getWordAtIndex,
	formatSwitchName
};
