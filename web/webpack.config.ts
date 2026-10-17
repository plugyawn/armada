/// <reference types="webpack-dev-server" />

import child_process from "node:child_process";
import path from "node:path";

import type {ZopfliOptions} from "@gfx/zopfli";
import {gzip} from "@gfx/zopfli";
import CompressionPlugin from "compression-webpack-plugin";
import CssMinimizerPlugin from "css-minimizer-webpack-plugin";
import HtmlWebpackPlugin from "html-webpack-plugin";
import MiniCssExtractPlugin from "mini-css-extract-plugin";
import webpack from "webpack";
import BundleTracker from "webpack-bundle-tracker";

import DebugRequirePlugin from "./debug-require-webpack-plugin.ts";
import assets from "./webpack.assets.json" with {type: "json"};
import dev_assets from "./webpack.dev-assets.json" with {type: "json"};

// The licensed typeface's files are outside git, in a directory that may
// be elsewhere (ARMADA_FONT_DIR). tools/setup/build_armada_fonts turns
// whatever is there into web/generated/armada_fonts.css; running it at the
// start of every build, and watching the directory, makes dropping the
// files in the whole installation.
class ArmadaFontsPlugin implements webpack.WebpackPluginInstance {
    apply(compiler: webpack.Compiler): void {
        const fontDir = path.resolve(
            process.env["ARMADA_FONT_DIR"] ?? path.join(import.meta.dirname, "fonts/licensed"),
        );
        const build = (): void => {
            child_process.execFileSync(
                path.resolve(import.meta.dirname, "../tools/setup/build_armada_fonts"),
                ["--quiet"],
                {stdio: "inherit"},
            );
        };
        compiler.hooks.beforeRun.tap("ArmadaFontsPlugin", build);
        compiler.hooks.watchRun.tap("ArmadaFontsPlugin", build);
        compiler.hooks.afterCompile.tap("ArmadaFontsPlugin", (compilation) => {
            compilation.contextDependencies.add(fontDir);
        });
    }
}

const config = (
    env: {
        minimize?: true;
        puppeteer_tests?: true;
        ZULIP_VERSION?: string;
        custom_5xx_file?: string;
    } = {},
    argv: {mode?: string},
): webpack.Configuration[] => {
    const production: boolean = argv.mode === "production";

    const baseConfig: webpack.Configuration = {
        mode: production ? "production" : "development",
        context: import.meta.dirname,
        cache: {
            type: "filesystem",
            buildDependencies: {
                config: [import.meta.filename],
            },
        },
    };

    const plugins: webpack.WebpackPluginInstance[] = [
        new webpack.DefinePlugin({
            DEVELOPMENT: JSON.stringify(!production),
            ZULIP_VERSION: JSON.stringify(env.ZULIP_VERSION ?? "development"),
        }),
        new DebugRequirePlugin(),
        new ArmadaFontsPlugin(),
        new BundleTracker({
            path: path.join(import.meta.dirname, production ? ".." : "../var"),
            filename: production ? "webpack-stats-production.json" : "webpack-stats-dev.json",
        }),
        // Extract CSS from files
        new MiniCssExtractPlugin({
            filename: production ? "[name].[contenthash].css" : "[name].css",
            chunkFilename: production ? "[contenthash].css" : "[id].css",
        }),
        new HtmlWebpackPlugin({
            filename: "5xx.html",
            template: env.custom_5xx_file ? "html/" + env.custom_5xx_file : "html/5xx.html",
            // Armada's own page is drawn like the other pages before the
            // app; a server's custom page keeps the old styles.
            chunks: [env.custom_5xx_file ? "error-styles" : "armada-portico"],
            publicPath: production ? "/static/webpack-bundles/" : "/webpack/",
        }),
    ];
    if (production && !env.puppeteer_tests) {
        plugins.push(
            new CompressionPlugin<ZopfliOptions>({
                // Use zopfli to write pre-compressed versions of text files
                test: /\.(js|css|html)$/,
                algorithm: gzip,
            }),
        );
    }

    // Everything that is not the rebuilt frontend goes into the cascade
    // layer `zulip`, below it; see zulip-layer-loader.ts.
    const armadaDir = path.resolve(import.meta.dirname, "src/armada");
    const stylesDir = path.resolve(import.meta.dirname, "styles");
    const zulipLayerLoader = path.resolve(import.meta.dirname, "zulip-layer-loader.ts");

    const frontendConfig: webpack.Configuration = {
        ...baseConfig,
        name: "frontend",
        entry: production
            ? process.env["ZULIP_DEV_PRODUCTION_ASSETS"] === "1"
                ? {...assets, "dev-login": dev_assets["dev-login"]}
                : assets
            : Object.fromEntries(
                  Object.entries({...assets, ...dev_assets}).map(([name, paths]) => [
                      name,
                      [...paths, "./src/debug.ts"],
                  ]),
              ),
        module: {
            rules: [
                {
                    test: path.resolve(import.meta.dirname, "src/zulip_test.ts"),
                    loader: "expose-loader",
                    options: {exposes: "zulip_test"},
                },
                {
                    test: path.resolve(import.meta.dirname, "debug-require.cjs"),
                    loader: "expose-loader",
                    options: {exposes: "require"},
                },
                // Generate webfont
                {
                    test: /\.font\.cjs$/,
                    use: [
                        MiniCssExtractPlugin.loader,
                        {
                            loader: "css-loader",
                            options: {
                                url: false, // webfonts-loader generates public relative URLs
                            },
                        },
                        zulipLayerLoader,
                        {
                            loader: "webfonts-loader",
                            options: {
                                fileName: production
                                    ? "files/[fontname].[chunkhash].[ext]"
                                    : "files/[fontname].[ext]",
                                publicPath: "",
                            },
                        },
                    ],
                    type: "javascript/auto",
                },
                // Transpile .js, .ts and .tsx files with Babel
                {
                    test: /\.[cm]?[jt]sx?$/,
                    include: [path.resolve(import.meta.dirname, "src")],
                    loader: "babel-loader",
                },
                // Styles written at run time: CodeMirror's, by style-mod, and
                // the altcha widget's own.
                {
                    test: /[/\\]style-mod[/\\](src|dist)[/\\]style-mod\.c?js$/,
                    loader: zulipLayerLoader,
                    options: {injectedStyle: ["this.styleTag.textContent", "text"]},
                },
                {
                    test: /[/\\]altcha[/\\]dist[/\\]altcha\.js$/,
                    loader: zulipLayerLoader,
                    options: {injectedStyle: ["r.textContent", "e"]},
                },
                // The emoji sprite sheets are imported in emojisets.ts with
                // inline loaders, which skip the rules below but not a
                // pre-loader.
                {
                    test: /[/\\]generated[/\\]emoji-styles[/\\][^/\\]+\.css$/,
                    enforce: "pre",
                    loader: zulipLayerLoader,
                },
                // The rebuilt frontend's stylesheets: native CSS, layered
                // by their own @layer statements.
                {
                    test: /\.css$/,
                    include: armadaDir,
                    sideEffects: true,
                    use: [
                        MiniCssExtractPlugin.loader,
                        {
                            loader: "css-loader",
                            options: {
                                sourceMap: true,
                            },
                        },
                    ],
                },
                // regular css files
                {
                    test: /\.css$/,
                    exclude: [stylesDir, armadaDir],
                    // Bare CSS imports are loaded for their effect; without
                    // this, packages shipping "sideEffects": false (e.g.
                    // @milkdown/crepe) get their stylesheets tree-shaken.
                    sideEffects: true,
                    use: [
                        MiniCssExtractPlugin.loader,
                        {
                            loader: "css-loader",
                            options: {
                                // A sheet that another @imports is wrapped too.
                                importLoaders: 1,
                                sourceMap: true,
                            },
                        },
                        zulipLayerLoader,
                    ],
                },
                // PostCSS loader
                {
                    test: /\.css$/,
                    include: stylesDir,
                    use: [
                        MiniCssExtractPlugin.loader,
                        {
                            loader: "css-loader",
                            options: {
                                importLoaders: 2,
                                sourceMap: true,
                            },
                        },
                        // After PostCSS, so that postcss-preset-env never
                        // sees the layer and cannot try to polyfill it.
                        zulipLayerLoader,
                        {
                            loader: "postcss-loader",
                            options: {
                                sourceMap: true,
                            },
                        },
                    ],
                },
                {
                    test: /\.hbs$/,
                    loader: "handlebars-loader",
                    options: {
                        ignoreHelpers: true,
                        // Tell webpack not to explicitly require these.
                        knownHelpers: [
                            // The ones below are defined in web/src/templates.ts
                            "eq",
                            "and",
                            "or",
                            "not",
                            "t",
                            "tr",
                            "map_entries",
                            "object_entries",
                            "object_values",
                            "rendered_markdown",
                            "numberFormat",
                            "tooltip_hotkey_hints",
                            "popover_hotkey_hints",
                            "list_each",
                        ],
                        precompileOptions: {
                            knownHelpersOnly: true,
                            strict: true,
                            explicitPartialContext: true,
                        },
                        preventIndent: true,
                        // This replaces relative image resources with
                        // a computed require() path to them, so their
                        // webpack-hashed URLs are used.
                        inlineRequires: /^(\.\.\/)+(images|static)\//,
                    },
                },
                // load fonts and files
                {
                    test: /\.(eot|jpg|svg|ttf|otf|png|webp|woff2?)$/,
                    type: "asset/resource",
                },
            ],
        },
        output: {
            path: path.resolve(import.meta.dirname, "../static/webpack-bundles"),
            publicPath: "auto",
            filename: production ? "[name].[contenthash].js" : "[name].js",
            assetModuleFilename: production
                ? "files/[name].[hash][ext][query]"
                : // Avoid directory traversal bug that upstream won't fix
                  // (https://github.com/webpack/webpack/issues/11937)
                  (pathData) => "files" + path.join("/", pathData.filename!),
            chunkFilename: production ? "[contenthash].js" : "[id].js",
            crossOriginLoading: "anonymous",
        },
        // We prefer cheap-module-source-map over any eval-* options
        // because stacktrace-gps doesn't currently support extracting
        // the source snippets with the eval-* options.
        devtool: production ? "source-map" : "cheap-module-source-map",
        optimization: {
            minimize: env.minimize ?? production,
            minimizer: [
                new CssMinimizerPlugin({
                    minify: CssMinimizerPlugin.cleanCssMinify,
                }),
                "...",
            ],
            splitChunks: {
                chunks: "all",
                // webpack/examples/many-pages suggests 20 requests for HTTP/2
                maxAsyncRequests: 20,
                maxInitialRequests: 20,
            },
        },
        plugins,
        devServer: {
            client: {
                overlay: {
                    runtimeErrors: false,
                },
            },
            devMiddleware: {
                publicPath: "/webpack/",
                stats: {
                    // We want just errors and a clear, brief notice
                    // whenever webpack compilation has finished.
                    preset: "minimal",
                    assets: false,
                    modules: false,
                },
            },
            headers: {
                "Access-Control-Allow-Origin": "*",
                "Timing-Allow-Origin": "*",
            },
            setupMiddlewares: (middlewares) =>
                middlewares.filter((middleware) => middleware.name !== "cross-origin-header-check"),
        },
        infrastructureLogging: {
            level: "warn",
        },
        watchOptions: {
            ignored: [
                "**/node_modules/**",
                // Prevent Emacs file locks from crashing webpack-dev-server
                // https://github.com/webpack/webpack-dev-server/issues/2821
                "**/.#*",
            ],
        },
    };

    const serverConfig: webpack.Configuration = {
        ...baseConfig,
        name: "server",
        target: "node",
        entry: {
            katex_server: "babel-loader!./server/katex_server.ts",
            "katex-cli": "shebang-loader!katex/cli",
            jdenticon: "shebang-loader!../node_modules/jdenticon/bin/jdenticon.js",
        },
        output: {
            path: path.resolve(import.meta.dirname, "../static/webpack-bundles"),
        },
    };

    return [frontendConfig, serverConfig];
};
export default config;
