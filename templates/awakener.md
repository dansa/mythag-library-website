---
title: Awakener Name
description: Builds and new-player guidance for Awakener Name.
template: awakeners/awakener.html
awakener:
  tagline: Short description of what this Awakener does
  roles:
    - Main role
    - Secondary role
  ranks:
    dps:
      - tier: B
        note: Decent
    support:
      - tier: A
        note: Great
  stopping_points:
    - E0
    - E3
  builds:
    - name: Example Build
      covenants:
        - covenant-id
      covenants_note: Optional guidance such as Any support
      wheels:
        early_game:
          - id: wheel-id
            note: Optional explanation
        astral_reign:
          - id: wheel-id
  suggested_posses:
    - id: posse-id
      note: Optional explanation
  suggested_posses_note: Optional note such as Any
  works_well_with:
    - other-awakener-id
  works_well_with_note: Optional note such as Anyone
---

Write the guide as ordinary Markdown here.
