// The pages before the app (templates/zerver/armada_portico.html): the
// rebuilt frontend's tokens, faces and kit, the portico's sheet and what
// its forms do in the page, and nothing of the old application.
import "../armada/tokens/layers.css";
import "../armada/tokens/fonts.css";
import "../../generated/armada_fonts.css";
import "../armada/tokens/tokens.css";
import "../armada/tokens/palette-lapis.css";
import "../armada/tokens/palette-tide.css";
import "../armada/tokens/palette-citrine.css";
import "../armada/tokens/palette-onyx.css";
import "../armada/tokens/palette-shared.css";
// The kit's sheets in the kit's own order (kit/index.ts): in another, webpack
// cannot order the chunk the app and these pages share, and says so over
// every page the dev server serves.
import "../armada/kit/base.css";
import "../armada/kit/icons.css";
import "../armada/kit/hover.css";
import "../armada/kit/controls.css";
import "../armada/surfaces/portico/portico.css";
import "../armada/surfaces/portico/index.ts";
