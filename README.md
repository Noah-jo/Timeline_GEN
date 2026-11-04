# Noah Programme

Standalone project timeline and action-checklist workspace, published with GitHub Pages.

## Source

`index.html` is the deployment source. The standalone HTML contains its styles, selected Lucide icons, and application logic. It can also be opened directly in a browser. Browser data is kept in the existing local-storage keys; Firebase configuration and access rules are unchanged.

Cloud unlock only enables browsing. Loading a cloud project creates a local editing copy. Auto Sync starts only after an explicit Sync action. Independent changes are merged against the last shared version; conflicting fields require a choice. Local browser tabs also coordinate changes without enabling Cloud.

## Verification

Use Python 3 with Selenium and an available Chrome browser:

```powershell
python tests/test_upgrade.py
python tests/test_workspace_scenarios.py
python tests/test_visual_layout.py
```

The tests remove the production Firebase module and use isolated browser profiles with in-memory test doubles. They do not modify production projects. Screenshots and PDF samples are written to a temporary directory reported by each test.

## Release

Back up the current HTML before replacing `index.html`. Push the verified source and tests to `main`, then verify the GitHub Pages build and the live build identifier (`document.documentElement.dataset.build`). Local working copies are mirrors, not alternate deployment entry points.

Selected Lucide v0.468.0 icons are distributed under the ISC license; their license notice is embedded in the HTML source.
