import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import BoltIcon from '@mui/icons-material/Bolt';
import EmojiObjectsIcon from '@mui/icons-material/EmojiObjects';
import PsychologyIcon from '@mui/icons-material/Psychology';
import SpaIcon from '@mui/icons-material/Spa';
import ShuFeatherIcon from './ShuFeatherIcon';

export const SHU_FEATHER_ID = 'shu_feather';

export const CURATED_AVATARS = [
  { id: SHU_FEATHER_ID, label: 'Shu feather', component: ShuFeatherIcon },
  { id: 'auto_awesome', label: 'Sparkle', component: AutoAwesomeIcon },
  { id: 'psychology', label: 'Brain', component: PsychologyIcon },
  { id: 'emoji_objects', label: 'Lightbulb', component: EmojiObjectsIcon },
  { id: 'bolt', label: 'Bolt', component: BoltIcon },
  { id: 'spa', label: 'Leaf', component: SpaIcon },
];

export function resolveCuratedAvatar(curatedId) {
  return CURATED_AVATARS.find((entry) => entry.id === curatedId) || CURATED_AVATARS[0];
}
