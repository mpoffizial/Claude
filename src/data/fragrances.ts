export interface Fragrance {
  number: string
  name: string
  family: string
  pyramid: {
    head: string
    heart: string
    base: string
  }
  description: string
}

export const fragrances: Fragrance[] = [
  {
    number: '№ 01',
    name: 'Umbra Nocte',
    family: 'Dark · Smoky · Resinous',
    pyramid: {
      head: 'Black pepper, bergamot zest, incense smoke',
      heart: 'Labdanum, dried tobacco leaf, rose oud',
      base: 'Birch tar, benzoin, vetiver, ambergris',
    },
    description:
      'A scent worn at the edge of night. Resin and burnt cedar coil around a single ember of rose, slow and unrepentant. It does not announce itself; it lingers in the room long after you have left it.',
  },
  {
    number: '№ 02',
    name: 'Sel de Nuage',
    family: 'Airy · Marine · Ozonic',
    pyramid: {
      head: 'Sea salt, pink grapefruit, ozone accord',
      heart: 'Water lily, ambrette seed, white tea',
      base: 'Driftwood, white musk, mineral amber',
    },
    description:
      'The weightless minute before rain over open water. Salt and ozone suspended in light, dried to a clean mineral skin. A fragrance that feels like breathing at altitude.',
  },
  {
    number: '№ 03',
    name: "Racine d'Or",
    family: 'Warm · Woody · Golden',
    pyramid: {
      head: 'Saffron, blood orange, cardamom',
      heart: 'Orris root, golden amber, sandalwood cream',
      base: 'Tonka bean, guaiac wood, warm honey',
    },
    description:
      'Roots pulled from sunlit earth, still warm. Saffron and orris melt into honeyed sandalwood — opulent, but never loud. The gold beneath the soil.',
  },
  {
    number: '№ 04',
    name: 'Cendre Douce',
    family: 'Soft · Powdery · Melancholic',
    pyramid: {
      head: 'Violet leaf, iris pallida, cool aldehydes',
      heart: 'Powdered suede, heliotrope, almond blossom',
      base: 'Cashmeran, grey musk, soft ash accord',
    },
    description:
      'The tenderness of ash gone cold. Powdered iris and suede settle into a quiet, almost mournful softness. A fragrance for the hour between memory and forgetting.',
  },
  {
    number: '№ 05',
    name: 'Forêt Noire',
    family: 'Green · Earthy · Mossy',
    pyramid: {
      head: 'Crushed fern, galbanum, juniper',
      heart: 'Wet oakmoss, violet leaf, pine resin',
      base: 'Damp earth, patchouli, vetiver root',
    },
    description:
      'Deep inside the black forest after rain. Green sap, broken stems and oakmoss over cold, living soil. Untamed, verdant, faintly wild — the scent of being far from anywhere.',
  },
  {
    number: '№ 06',
    name: 'Fièvre Blanche',
    family: 'White Floral · Clean · Magnetic',
    pyramid: {
      head: 'Neroli, green mandarin, dewy petals',
      heart: 'Tuberose, jasmine sambac, orange blossom',
      base: 'White amber, sheer musk, blond woods',
    },
    description:
      'A white-flower fever — luminous, almost narcotic, yet scrubbed clean. Tuberose and jasmine bloom against bright skin and blond woods. Magnetic; impossible to ignore.',
  },
]
