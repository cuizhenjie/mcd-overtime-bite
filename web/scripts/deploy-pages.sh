#!/usr/bin/env bash
# 发布产品页到 GitHub Pages（gh-pages 分支）
#
# 用法： bash scripts/deploy-pages.sh
#
# 产物是纯静态站点，没有运行时依赖；仓库不需要任何密钥。
# 唯一的外部依赖是 GitHub 的推送权限（走本机已有的 git 凭据）。
set -euo pipefail

cd "$(dirname "$0")/.."

REPO="${OB_PAGES_REPO:-https://github.com/cuizhenjie/mcd-overtime-bite.git}"
BRANCH="gh-pages"

echo "▸ 构建"
npm run build

DIST="$(mktemp -d)"
trap 'rm -rf "$DIST"' EXIT
cp -R dist/. "$DIST"/
# 关掉 Jekyll 处理，否则以 _ 开头的目录会被忽略
touch "$DIST/.nojekyll"

echo "▸ 推送到 $BRANCH"
cd "$DIST"
git init -q -b "$BRANCH"
git add -A
git -c user.name="mavis" -c user.email="mavis@local" commit -q -m "build: OvertimeBite 产品页"
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO"
git push --force origin "$BRANCH"

echo "▸ 完成：https://cuizhenjie.github.io/mcd-overtime-bite/"
echo "  （首次使用需先在仓库 Settings → Pages 选 Source: gh-pages 分支 / root）"
