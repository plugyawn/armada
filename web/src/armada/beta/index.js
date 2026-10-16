/* INTERIM BETA: the build entry of /beta/ ("armada-beta" in web/webpack.assets.json). Deleted with this directory when
   the rebuild's conversation lands; README.md lists everything outside it that goes too.
   Order matters: the cascade's order, the faces, the prototype's tokens and components, its glyphs and kit (each sets
   window globals), the motion and sound kits, then the page.
   The beta's sheets (the kit's two, beta.css, feed.css, pages.css, media.css, widgets.css, artifacts.css and previews.css) are wrapped in the layer `zulip.armada-beta`: a sublayer of the lowest layer the cascade
   declares (../tokens/layers.css), below the old sheets' own rules and every armada layer, wherever its statement
   happens to load. On /beta/ nothing else styles the page (the only other rules are @font-face), so it draws as it
   did; on any other page it could override nothing. */
import "../tokens/layers.css";
import "../tokens/fonts.css";
import "../../../generated/armada_fonts.css";
import "./kit/mock.css";
import "./kit/proto.css";
import "./beta.css";
import "./feed.css";
import "./pages.css";
import "./media.css";
import "./widgets.css";
import "./artifacts.css";
import "./previews.css";
import "./kit/glyphs.js";
import "./kit/marks.js";
import "./kit/kit.js";
import "./kit/motion.js";
import "./kit/sound.js";
import "./host.js";
