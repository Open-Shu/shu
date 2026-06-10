import BoltIcon from '@mui/icons-material/Bolt';
import CycloneIcon from '@mui/icons-material/Cyclone';
import EmojiObjectsIcon from '@mui/icons-material/EmojiObjects';
import FlareIcon from '@mui/icons-material/Flare';
import SpaIcon from '@mui/icons-material/Spa';
import ShuFeatherIcon from './ShuFeatherIcon';

export const SHU_FEATHER_ID = 'shu_feather';

export const CURATED_AVATARS = [
  { id: SHU_FEATHER_ID, label: 'Shu feather', component: ShuFeatherIcon },
  { id: 'flare', label: 'Sunburst', component: FlareIcon },
  { id: 'cyclone', label: 'Swirl', component: CycloneIcon },
  { id: 'emoji_objects', label: 'Lightbulb', component: EmojiObjectsIcon },
  { id: 'bolt', label: 'Bolt', component: BoltIcon },
  { id: 'spa', label: 'Leaf', component: SpaIcon },
];

export function resolveCuratedAvatar(curatedId) {
  return CURATED_AVATARS.find((entry) => entry.id === curatedId) || CURATED_AVATARS[0];
}
