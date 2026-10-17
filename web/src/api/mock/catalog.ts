// Demo catalog for the mock backend: public-domain books with hand-built graphs.
// Tuples keep the data compact; `buildGraph` expands them into wire types.

import type { Book, Community, GraphEntity, GraphEvent, GraphRelation, StoryGraph } from "../types";

type E = [key: string, name: string, type: string, community: number, description: string, pages: [number, number], mentions: number, aliases?: string[]];
type V = [key: string, summary: string, pos: number, chapter: string, page: number, involves: string[], quote: string];
type R = [src: string, dst: string, rel: string, description: string];
type C = [cid: number, title: string, summary: string];

export interface CatalogEntry {
  book: Omit<Book, "id" | "created_at" | "owner_id" | "visibility" | "status" | "error">;
  entities: E[];
  events: V[];
  rels: R[];
  communities: C[];
}

export const CATALOG: Record<string, CatalogEntry> = {
  oz: {
    book: {
      title: "The Wonderful Wizard of Oz",
      author: "L. Frank Baum",
      blurb: "A Kansas cyclone carries Dorothy to a land of witches, wonders and a wizard who may not be what he seems.",
      genre: "Fantasy",
      cover_hue: 150,
      stats: { chapters: 24, pages: 154 },
    },
    communities: [
      [1, "The Road of Yellow Brick", "Dorothy gathers companions who each seek something they believe they lack: brains, a heart, courage, and home."],
      [2, "Witches of the Four Quarters", "Power in Oz is split between good witches of the North and South and wicked witches of the East and West."],
      [3, "The Emerald City", "The Wizard rules from behind a mask of illusion, and his city is green only because everyone wears tinted spectacles."],
    ],
    entities: [
      ["dorothy", "Dorothy", "person", 1, "A little girl from Kansas who wants nothing more than to go home to Aunt Em.", [1, 154], 412],
      ["toto", "Toto", "animal", 1, "Dorothy's small black dog, who keeps her from being lonely and pulls back the Wizard's screen.", [1, 152], 96],
      ["scarecrow", "Scarecrow", "person", 1, "A straw man who believes he has no brains, yet thinks of every plan the group follows.", [18, 150], 188],
      ["tinman", "Tin Woodman", "person", 1, "A woodman of tin who longs for a heart, and weeps so easily he rusts.", [26, 150], 171, ["Tin Man"]],
      ["lion", "Cowardly Lion", "person", 1, "A lion who roars at everything because he is afraid, but is brave when it matters.", [34, 150], 154, ["the Lion"]],
      ["wizard", "The Wizard of Oz", "person", 3, "The Great and Terrible ruler of the Emerald City, actually a humbug balloonist from Omaha.", [44, 140], 133, ["Oz", "Oscar Diggs"]],
      ["westwitch", "Wicked Witch of the West", "person", 2, "The ruler of the Winkie Country, who fears water and the dark and wants the Silver Shoes.", [70, 98], 64],
      ["eastwitch", "Wicked Witch of the East", "person", 2, "A witch killed when Dorothy's house lands on her, leaving only her Silver Shoes.", [8, 12], 14],
      ["glinda", "Glinda", "person", 2, "The Good Witch of the South, who reveals that the Silver Shoes could always carry Dorothy home.", [138, 152], 38],
      ["northwitch", "Good Witch of the North", "person", 2, "A kind old witch who kisses Dorothy's forehead for protection and points her to the Emerald City.", [9, 14], 21],
      ["shoes", "Silver Shoes", "object", 2, "Enchanted shoes taken from the Witch of the East. Three heel-clicks will carry the wearer anywhere.", [12, 152], 41],
      ["emerald", "Emerald City", "place", 3, "The capital of Oz, glittering green to anyone wearing the Guardian's spectacles.", [40, 140], 87],
      ["kansas", "Kansas", "place", 1, "Dorothy's grey, sunburned prairie home, where Uncle Henry and Aunt Em live.", [1, 154], 52],
      ["monkeys", "Winged Monkeys", "group", 2, "Creatures bound to obey whoever holds the Golden Cap, three times each.", [78, 134], 33],
      ["cap", "Golden Cap", "object", 2, "A charm that commands the Winged Monkeys.", [78, 134], 19],
    ],
    events: [
      ["e1", "A cyclone lifts Dorothy's house from Kansas and carries it to Oz.", 1000, "Ch. 1", 4, ["dorothy", "toto", "kansas"], "The house whirled around two or three times and rose slowly through the air."],
      ["e2", "The house lands on the Wicked Witch of the East, killing her.", 2000, "Ch. 2", 9, ["dorothy", "eastwitch", "northwitch"], "There, sticking out from under a block of wood, were two feet, shod in silver shoes."],
      ["e3", "Dorothy takes the Silver Shoes and sets out on the yellow brick road.", 2500, "Ch. 2", 12, ["dorothy", "shoes", "northwitch"], "The road to the City of Emeralds is paved with yellow brick."],
      ["e4", "Dorothy lifts the Scarecrow off his pole; he joins her to ask for brains.", 3000, "Ch. 3", 18, ["dorothy", "scarecrow"], "I don't know enough. My head is stuffed with straw."],
      ["e5", "Dorothy oils the rusted Tin Woodman, who joins to ask for a heart.", 5000, "Ch. 5", 26, ["dorothy", "tinman"], "Once I had brains, and a heart also; so, having tried them both, I should much rather have a heart."],
      ["e6", "The Cowardly Lion attacks, is scolded by Dorothy, and joins to ask for courage.", 6000, "Ch. 6", 34, ["dorothy", "lion", "toto"], "It is a mystery to me how a lion could be such a coward."],
      ["e7", "The travellers enter the Emerald City and each sees the Wizard in a different form.", 11000, "Ch. 11", 52, ["wizard", "emerald", "dorothy"], "When Oz spoke, it was a great Head, without a body to support it."],
      ["e8", "The Wizard demands they kill the Wicked Witch of the West.", 11500, "Ch. 11", 58, ["wizard", "westwitch"], "Kill the Wicked Witch of the West."],
      ["e9", "The Witch captures them using the Winged Monkeys and the Golden Cap.", 12000, "Ch. 12", 78, ["westwitch", "monkeys", "cap", "dorothy"], "Go to the strangers who are within my land and destroy them all except the Lion."],
      ["e10", "Dorothy throws a bucket of water on the Witch, who melts away.", 12500, "Ch. 12", 86, ["dorothy", "westwitch", "shoes"], "See! I am melting away!"],
      ["e11", "Toto knocks over a screen, revealing the Wizard as an ordinary man.", 15000, "Ch. 15", 110, ["toto", "wizard"], "I am just a common man."],
      ["e12", "The Wizard gives bran-brains, a silk heart and a draught of courage.", 16000, "Ch. 16", 118, ["wizard", "scarecrow", "tinman", "lion"], "How can I help being a humbug when all these people make me do things everybody knows can't be done?"],
      ["e13", "The Wizard's balloon leaves without Dorothy.", 17000, "Ch. 17", 124, ["wizard", "dorothy", "toto"], "Come back! I want to go, too!"],
      ["e14", "Glinda reveals the Silver Shoes can carry Dorothy home; she clicks her heels.", 23000, "Ch. 23", 150, ["glinda", "shoes", "dorothy", "kansas"], "Take me home to Aunt Em!"],
    ],
    rels: [
      ["dorothy", "toto", "OWNS", "Toto is Dorothy's dog."],
      ["dorothy", "scarecrow", "BEFRIENDS", "Rescued him from his pole."],
      ["dorothy", "tinman", "BEFRIENDS", "Oiled his joints."],
      ["dorothy", "lion", "BEFRIENDS", "Scolded him, then welcomed him."],
      ["dorothy", "kansas", "LONGS_FOR", "She wants to return home."],
      ["dorothy", "shoes", "WEARS", "Took them from the dead witch."],
      ["dorothy", "westwitch", "DEFEATS", "Melts her with water."],
      ["dorothy", "eastwitch", "KILLS", "Her house lands on the witch."],
      ["wizard", "emerald", "RULES", "Rules the city from behind a screen."],
      ["wizard", "westwitch", "FEARS", "Secretly afraid of the witches."],
      ["westwitch", "cap", "HOLDS", "Uses its three wishes."],
      ["cap", "monkeys", "COMMANDS", "The cap binds the monkeys."],
      ["westwitch", "shoes", "COVETS", "Wants the Silver Shoes."],
      ["eastwitch", "shoes", "OWNED", "Original owner of the shoes."],
      ["glinda", "shoes", "REVEALS", "Explains their power."],
      ["northwitch", "dorothy", "PROTECTS", "Her kiss marks Dorothy."],
      ["scarecrow", "emerald", "RULES_LATER", "Left in charge after the Wizard leaves."],
      ["toto", "wizard", "EXPOSES", "Knocks over the screen."],
      ["lion", "scarecrow", "PROTECTS", "Carries the group through danger."],
      ["tinman", "scarecrow", "BEFRIENDS", "Companions on the road."],
    ],
  },

  alice: {
    book: {
      title: "Alice's Adventures in Wonderland",
      author: "Lewis Carroll",
      blurb: "A curious girl follows a White Rabbit down a hole and into a world where logic has quietly packed its bags.",
      genre: "Fantasy",
      cover_hue: 330,
      stats: { chapters: 12, pages: 96 },
    },
    communities: [
      [1, "Down the Rabbit Hole", "Alice's changes in size mirror her uncertainty about who she is."],
      [2, "The Queen's Court", "A court of playing cards where every verdict comes before the evidence."],
    ],
    entities: [
      ["alice", "Alice", "person", 1, "A curious, polite and stubborn girl who keeps trying to make sense of nonsense.", [1, 96], 386],
      ["rabbit", "White Rabbit", "person", 1, "A nervous rabbit with a pocket watch, always late for the Duchess.", [1, 90], 58],
      ["hatter", "The Hatter", "person", 1, "Host of an endless tea party where it is always six o'clock.", [52, 88], 47, ["Mad Hatter"]],
      ["cat", "Cheshire Cat", "animal", 1, "A grinning cat who vanishes until only the grin remains.", [44, 70], 31],
      ["queen", "Queen of Hearts", "person", 2, "A furious monarch whose answer to every problem is 'Off with their heads!'", [62, 94], 71],
      ["knave", "Knave of Hearts", "person", 2, "Accused of stealing the Queen's tarts.", [82, 94], 18],
      ["caterpillar", "Caterpillar", "person", 1, "A hookah-smoking caterpillar who asks Alice 'Who are YOU?'", [34, 40], 22],
      ["wonderland", "Wonderland", "place", 1, "The dream-world beneath the rabbit hole.", [2, 96], 40],
    ],
    events: [
      ["a1", "Alice follows the White Rabbit down the rabbit hole.", 1000, "Ch. 1", 2, ["alice", "rabbit", "wonderland"], "Down, down, down. Would the fall never come to an end?"],
      ["a2", "The Caterpillar asks Alice who she is.", 5000, "Ch. 5", 34, ["alice", "caterpillar"], "Who are YOU?"],
      ["a3", "The Cheshire Cat explains that everyone here is mad.", 6000, "Ch. 6", 46, ["alice", "cat"], "We're all mad here. I'm mad. You're mad."],
      ["a4", "Alice joins the Hatter's mad tea-party.", 7000, "Ch. 7", 52, ["alice", "hatter"], "Why is a raven like a writing-desk?"],
      ["a5", "The Queen orders executions during croquet.", 8000, "Ch. 8", 64, ["queen", "alice", "cat"], "Off with her head!"],
      ["a6", "The Knave is tried for stealing the tarts; Alice wakes up.", 12000, "Ch. 12", 92, ["knave", "queen", "alice"], "You're nothing but a pack of cards!"],
    ],
    rels: [
      ["alice", "rabbit", "FOLLOWS", "Chases him into Wonderland."],
      ["alice", "hatter", "MEETS", "Attends his tea party."],
      ["alice", "cat", "TALKS_TO", "Asks it for directions."],
      ["queen", "knave", "ACCUSES", "Puts him on trial."],
      ["alice", "queen", "DEFIES", "Refuses to be silenced at the trial."],
      ["rabbit", "queen", "SERVES", "Acts as herald at court."],
      ["alice", "caterpillar", "QUESTIONED_BY", "Asked who she is."],
      ["alice", "wonderland", "EXPLORES", "Her dream journey."],
    ],
  },

  pride: {
    book: {
      title: "Pride and Prejudice",
      author: "Jane Austen",
      blurb: "Elizabeth Bennet and Mr. Darcy misjudge each other completely, and slowly, wittily, they learn better.",
      genre: "Romance",
      cover_hue: 15,
      stats: { chapters: 61, pages: 279 },
    },
    communities: [
      [1, "Longbourn", "The Bennet household and the pressure to marry well."],
      [2, "Pemberley & Netherfield", "Wealthy circles where pride and first impressions are tested."],
    ],
    entities: [
      ["elizabeth", "Elizabeth Bennet", "person", 1, "Quick-witted second Bennet daughter who trusts her first impressions too much.", [1, 279], 601, ["Lizzy", "Eliza"]],
      ["darcy", "Mr. Darcy", "person", 2, "A proud, wealthy gentleman of Pemberley whose reserve hides real integrity.", [8, 279], 412],
      ["jane", "Jane Bennet", "person", 1, "The eldest Bennet sister, gentle and inclined to think well of everyone.", [2, 270], 214],
      ["bingley", "Mr. Bingley", "person", 2, "Darcy's amiable friend, who falls for Jane.", [2, 270], 188],
      ["wickham", "Mr. Wickham", "person", 1, "A charming officer with a hidden history of deceit.", [62, 250], 141],
      ["lydia", "Lydia Bennet", "person", 1, "The youngest Bennet sister, reckless and flirtatious.", [5, 260], 120],
      ["collins", "Mr. Collins", "person", 1, "The Bennets' pompous clergyman cousin and heir to Longbourn.", [48, 200], 96],
      ["pemberley", "Pemberley", "place", 2, "Darcy's estate in Derbyshire.", [180, 279], 44],
    ],
    events: [
      ["p1", "Darcy slights Elizabeth at the Meryton assembly.", 1000, "Ch. 3", 9, ["darcy", "elizabeth"], "She is tolerable, but not handsome enough to tempt me."],
      ["p2", "Elizabeth refuses Mr. Collins's proposal.", 19000, "Ch. 19", 82, ["elizabeth", "collins"], "You could not make me happy, and I am convinced that I am the last woman in the world who would make you so."],
      ["p3", "Darcy proposes at Hunsford and is refused.", 34000, "Ch. 34", 150, ["darcy", "elizabeth"], "In vain have I struggled. It will not do."],
      ["p4", "Darcy's letter reveals the truth about Wickham.", 35000, "Ch. 35", 156, ["darcy", "wickham", "elizabeth"], "Till this moment I never knew myself."],
      ["p5", "Lydia elopes with Wickham; Darcy secretly arranges their marriage.", 46000, "Ch. 46", 214, ["lydia", "wickham", "darcy"], "She is lost for ever."],
      ["p6", "Elizabeth accepts Darcy's second proposal.", 58000, "Ch. 58", 266, ["elizabeth", "darcy"], "You are too generous to trifle with me."],
    ],
    rels: [
      ["darcy", "elizabeth", "LOVES", "Grows to admire her."],
      ["elizabeth", "wickham", "TRUSTS", "Believes his story at first."],
      ["wickham", "darcy", "WRONGS", "Slanders Darcy."],
      ["bingley", "jane", "LOVES", "Courts her at Netherfield."],
      ["darcy", "bingley", "ADVISES", "Persuades him to leave Jane."],
      ["lydia", "wickham", "ELOPES_WITH", "Runs off to London."],
      ["elizabeth", "jane", "SISTER_OF", "Closest sisters."],
      ["collins", "elizabeth", "PROPOSES_TO", "Rejected."],
      ["darcy", "pemberley", "OWNS", "His estate."],
    ],
  },

  frankenstein: {
    book: {
      title: "Frankenstein",
      author: "Mary Shelley",
      blurb: "A young scientist gives life to a creature, then abandons it, and both pay for that choice.",
      genre: "Gothic",
      cover_hue: 210,
      stats: { chapters: 24, pages: 220 },
    },
    communities: [
      [1, "The Creator", "Victor's ambition, guilt and ruin."],
      [2, "The Creature", "Rejection turns a gentle being toward vengeance."],
    ],
    entities: [
      ["victor", "Victor Frankenstein", "person", 1, "A Genevan student obsessed with uncovering the secret of life.", [12, 220], 344],
      ["creature", "The Creature", "person", 2, "Victor's creation: articulate, sensitive, and driven to vengeance by rejection.", [50, 220], 280, ["the monster", "the daemon"]],
      ["elizabeth_l", "Elizabeth Lavenza", "person", 1, "Victor's adopted sister and later his bride.", [20, 190], 98],
      ["walton", "Robert Walton", "person", 1, "Arctic explorer who records Victor's story.", [1, 220], 60],
      ["delacey", "The De Laceys", "group", 2, "A cottager family the Creature secretly learns from.", [96, 130], 52],
    ],
    events: [
      ["f1", "Victor brings the Creature to life and flees in horror.", 5000, "Ch. 5", 50, ["victor", "creature"], "I beheld the wretch, the miserable monster whom I had created."],
      ["f2", "The Creature learns language by watching the De Laceys.", 12000, "Ch. 12", 104, ["creature", "delacey"], "I learned and applied the words fire, milk, bread, and wood."],
      ["f3", "The De Laceys drive the Creature away.", 15000, "Ch. 15", 128, ["creature", "delacey"], "I am an unfortunate and deserted creature."],
      ["f4", "The Creature demands a companion.", 17000, "Ch. 17", 146, ["creature", "victor"], "You must create a female for me."],
      ["f5", "The Creature kills Elizabeth on her wedding night.", 23000, "Ch. 23", 190, ["creature", "elizabeth_l", "victor"], "I shall be with you on your wedding-night."],
    ],
    rels: [
      ["victor", "creature", "CREATES", "Animates and abandons it."],
      ["creature", "victor", "HUNTS", "Seeks revenge."],
      ["victor", "elizabeth_l", "MARRIES", "Their wedding night ends in death."],
      ["creature", "delacey", "OBSERVES", "Learns humanity from them."],
      ["walton", "victor", "RESCUES", "Takes him aboard in the Arctic."],
    ],
  },

  treasure: {
    book: {
      title: "Treasure Island",
      author: "Robert Louis Stevenson",
      blurb: "A boy, a map, and a one-legged cook with a parrot. What could possibly go wrong at sea?",
      genre: "Adventure",
      cover_hue: 40,
      stats: { chapters: 34, pages: 240 },
    },
    communities: [
      [1, "The Hispaniola", "Loyal crew against mutineers."],
      [2, "Flint's Treasure", "The legend and the map that drive everyone."],
    ],
    entities: [
      ["jim", "Jim Hawkins", "person", 1, "A brave innkeeper's son who narrates the voyage.", [1, 240], 300],
      ["silver", "Long John Silver", "person", 1, "The charming one-legged cook who leads the mutiny.", [40, 240], 260],
      ["map", "Flint's Map", "object", 2, "The treasure map found in Billy Bones's sea chest.", [20, 230], 48],
      ["flint", "Captain Flint", "person", 2, "The dead pirate who buried the treasure.", [10, 230], 40],
      ["gunn", "Ben Gunn", "person", 2, "A marooned pirate who has already moved the treasure.", [100, 236], 52],
    ],
    events: [
      ["t1", "Jim finds Flint's map in Billy Bones's chest.", 4000, "Ch. 4", 24, ["jim", "map", "flint"], "The map of an island, with latitude and longitude."],
      ["t2", "Jim overhears Silver plotting mutiny from the apple barrel.", 11000, "Ch. 11", 70, ["jim", "silver"], "What I heard in the apple barrel."],
      ["t3", "Jim meets Ben Gunn on the island.", 15000, "Ch. 15", 100, ["jim", "gunn"], "I'm poor Ben Gunn, I am."],
      ["t4", "The pirates find the treasure pit empty.", 33000, "Ch. 33", 230, ["silver", "gunn", "map"], "The cache had been found and rifled."],
    ],
    rels: [
      ["jim", "silver", "BEFRIENDS", "An uneasy friendship."],
      ["silver", "map", "SEEKS", "Plots to seize it."],
      ["flint", "map", "DREW", "Marked the treasure."],
      ["gunn", "flint", "SAILED_WITH", "A former crewman."],
      ["jim", "gunn", "ALLIES_WITH", "Helps the loyal crew."],
    ],
  },
};

export function buildGraph(entry: CatalogEntry): StoryGraph {
  const entities: GraphEntity[] = entry.entities.map(([key, name, type, community, description, [fp, lp], mentions, aliases]) => ({
    key,
    name,
    type,
    aliases: aliases ?? [],
    description,
    first_page: fp,
    last_page: lp,
    mention_count: mentions,
    community_id: community,
    sid: null,
  }));
  const events: GraphEvent[] = entry.events.map(([key, summary, pos, chapter, page, involves]) => ({
    key,
    summary,
    story_pos: pos,
    chapter,
    first_page: page,
    sid: null,
    involves,
  }));
  const relations: GraphRelation[] = entry.rels.map(([src, dst, rel, description], i) => ({
    id: `r${i}`,
    src,
    dst,
    rel,
    description,
    weight: 1,
    sid: null,
  }));
  const communities: Community[] = entry.communities.map(([cid, title, summary]) => ({ cid, title, summary }));
  return { entities, events, relations, communities, hidden: [] };
}

/** Evidence quotes by event key, for citations. */
export function quotesOf(entry: CatalogEntry): Record<string, string> {
  return Object.fromEntries(entry.events.map((v) => [v[0], v[6]]));
}
