---
title: Pourquoi une tâche revient dans la boîte de réception
type: explanation
description: Comprendre les colonnes Inbox et Plus tard dans les projets et les tableaux personnels.
lang: fr
persona: everyone
app: Project
models:
  - project.task
  - project.task.type
source:
  - custom-addons/usl_project/models/project_task.py
  - custom-addons/usl_project/models/project_task_type.py
generated: false
---

# Pourquoi une tâche revient dans la boîte de réception

L’Inbox est l’étape qu’Odoo propose pour une nouvelle tâche lorsque vous
n’indiquez aucune colonne. C’est aussi la destination des rappels. Une étape
peut remplir ce rôle sous un autre nom, comme « New ». Dans **Mes tâches**, le
premier classement de chaque personne sert de boîte de réception personnelle.

Une tâche ouverte placée dans **Plus tard** peut revenir lorsque son début
planifié est atteint, que son échéance approche, ou que son avancement change.
L’icône ⓘ de la colonne donne la liste des déclencheurs. Odoo vérifie les dates
dès qu’elles changent et toutes les heures. Les échéances sont évaluées en jours
calendaires dans le fuseau de la société pour les projets et dans celui de la
personne pour son tableau personnel.

La pastille sous le titre indique la raison du retour. Le compteur **+1** ou
**+2** ouvre la liste lorsqu’un même retour a plusieurs motifs. La pastille
reste visible jusqu’à la sortie de l’Inbox, à la clôture ou à l’archivage.
