# Personal Website

#### To Deploy
cd quick-start
make github

ghp-import -m "Generate Pelican site" -b $(GITHUB_PAGES_BRANCH) "$(OUTPUTDIR)"