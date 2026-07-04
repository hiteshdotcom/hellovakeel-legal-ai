// Single source of truth for iconography (Phosphor). Re-exported under stable
// local names so the icon library can be swapped in one place. Global weight/
// size defaults are set via <IconContext> in App.tsx.
export {
  Scales,
  Plus,
  MagnifyingGlass as Search,
  Question,
  List as Menu,
  X,
  Sun,
  Moon,
  Check,
  Warning,
  PaperPlaneRight as Send,
  ShieldCheck as Shield,
  Brain,
  Sparkle,
  Star,
  BookmarkSimple as Bookmark,
  FileText,
  Target,
  Lightbulb,
  CaretLeft,
  CaretRight,
  CaretDown,
  DotsThree,
  Copy,
  ThumbsUp,
  ThumbsDown,
  ArrowLeft,
  ArrowRight,
  Lightning,
  SignOut,
  ShareNetwork as Graph,
  SpinnerGap,
  ArrowsOutSimple as Expand,
  GoogleLogo,
} from "@phosphor-icons/react";

// Shared icon component type (Phosphor). Use this wherever a component takes an
// icon prop, so any icon from the set is assignable.
export type { Icon as IconType } from "@phosphor-icons/react";
