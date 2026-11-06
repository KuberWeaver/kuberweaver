# KuberWeaver, notes de présentation investisseurs

14 diapositives, thème clair, format 16:9. Public non technique.

## 01. KuberWeaver

Positionnement de vision : rendre les environnements propres à une entreprise reproductibles et gouvernés. Il ne s’agit pas de dire que tous les secteurs sont déjà couverts. Le projet est au stade prototype. Aucun montant levé, revenu ou client n’est revendiqué.

## 02. L’infrastructure devient un service interne.

L’infrastructure correspond aux moyens informatiques qui font fonctionner les applications : serveurs, stockage, réseaux et bases de données. L’IPE les transforme en produits internes consommables avec standardisation, automatisation et self-service. Le schéma original situe ces leviers entre les difficultés opérationnelles et les résultats métier. Les gains sont des objectifs, pas des chiffres mesurés pour KuberWeaver.[1][4]

## 03. Le sur-mesure finit trop souvent en bricolage.

Problèmes types à valider lors des entretiens clients. Le projet ne dispose pas d’une étude chiffrée de leur prévalence. Les difficultés de ticket-ops, de faible standardisation et de coûts non maîtrisés sont décrites dans la source IPE.[1]

## 04. Transformer votre savoir-faire en services réutilisables.

Le parcours de demande, approbation et déploiement est implémenté sur des services web du laboratoire. Les modèles réutilisables sont appelés blueprints. Le retour arrière dépend du service et n’est pas une garantie universelle.

## 05. Des briques métier autour d’un socle commun.

Un blueprint compatible apporte ses paramètres et son cycle de vie ; le formulaire standard est généré. Une nouvelle famille technique peut nécessiter un fournisseur développé et testé. L’adaptation n’est pas sans effort et les pages frontend spécialisées ne sont pas chargées automatiquement. L’intégration à l’existant et la portabilité des automatisations sont des objectifs, à éprouver chez les pilotes.

## 06. Le même socle. Des catalogues différents.

Cas illustratifs de positionnement. Industriels : environnements de test et contraintes de sites. Fintech : séparation des accès et traçabilité des changements. Artisanat : cas à qualifier, notamment pour les structures ayant un besoin récurrent d’environnements ; un petit artisan sans équipe IT pourrait être servi par son prestataire plutôt qu’exploiter le produit lui-même. Aucune conformité réglementaire automatique n’est revendiquée.

## 07. Des pratiques de grande entreprise, à votre échelle.

L’ambition est de rendre accessibles les mécanismes de gouvernance, sécurité et pilotage économique habituellement structurés dans les grandes organisations. Les rôles, approbations et estimations existent dans le prototype. La conformité, la résilience et l’égalité de niveau de sécurité avec une grande entreprise ne sont pas garanties. Le durcissement production reste à effectuer.

## 08. Une porte d’entrée, plusieurs capacités.

Schéma de vision original du projet, recadré pour enlever son titre et améliorer la lisibilité. Le lien Catbox fourni renvoyait un corps vide et n’a pas pu être utilisé ; cette illustration provient de docs/cadrage-original. OPA, Vault, OpenTofu, ChatOps et les cibles hybrides sont des éléments de vision : le prototype réel utilise des règles Python, une source OpenBao et un exécuteur Ansible sur des conteneurs web. Toutes les fonctions de ce schéma ne sont pas implémentées.

## 09. Hermes : un assistant outillé, pas un décideur.

Hermes Agent, développé par Nous Research, est un logiciel qui relie un modèle IA à des outils et à une mémoire. Il ne doit pas être confondu avec le modèle de langage lui-même. Le rôle envisagé dans KuberWeaver est un diagnostic optionnel en lecture seule, avec recommandations soumises à l’humain. Hermes peut techniquement agir selon ses outils et permissions ; le mode lecture seule est un choix d’intégration à faire respecter. Aucun agent Hermes de diagnostic n’est actuellement connecté au prototype ; le diagnostic livré est déterministe.[2]

## 10. Commencer petit. Étendre sans repartir de zéro.

Le noyau et les contrats de fournisseurs sont modulaires. La montée en charge entreprise reste un objectif de roadmap : SSO, permissions par objet, isolation, haute disponibilité et tests de charge ne sont pas livrés. On ne revendique donc pas une extensibilité technique infinie ni une capacité enterprise éprouvée.

## 11. Un budget pour les services utiles, pas pour le superflu.

Aucun prix de vente, pourcentage d’économie, coût client ou avantage chiffré face à ServiceNow n’a été validé. Les leviers proposés sont la réutilisation de l’existant, la réduction des gestes répétitifs et un périmètre proportionné. Le coût total inclut intégration, hébergement, maintenance et support. ServiceNow peut aussi être modulaire et auto-hébergé ; ce ne sont pas des exclusivités de KuberWeaver. La tarification, la licence et le modèle de support restent à définir.

## 12. Un prototype réel. Une industrialisation à construire.

Prototype : catalogue, canvas, rôles, approbation, déploiements réels NGINX/Apache sur deux cibles, canary/rolling, restauration, Git local et estimation interne. Tests relancés lors de la mise en dépôt privé : 159 backend, 35 frontend, build réussi. Ces tests ne constituent pas une certification sécurité ni une recette de production. Prochaines étapes à arbitrer : service réellement différent, secrets d’exécution, notifications, durcissement et validation terrain. Aucun client pilote acquis n’est revendiqué.

## 13. Six profils pour relier produit, infrastructure et valeur.

Équipe fournie par le porteur du projet. Les marchés et le modèle économique restent à valider. La prochaine conversation investisseurs peut porter sur des introductions à des entreprises pilotes et les critères de preuve attendus ; aucun besoin de financement chiffré n’est inventé.

## 14. Les références, les images et les limites.

Sources consultées pour la présentation. Le lien Catbox a renvoyé un corps vide et n’est pas embarqué ; le schéma de structure est le cadrage original local, sans affirmer que les deux images sont identiques. La pyramide est reproduite avec attribution à Platform Engineering ; droits de réutilisation à confirmer avant diffusion publique/commerciale. PDF produit directement depuis le même code de mise en page que le PPTX, pas par conversion de PowerPoint. Aucun push GitHub effectué pour cette présentation.[1][2][3][4]

## Provenance et limites de diffusion

La pyramide est fournie par Platform Engineering, avec attribution et lien vers son article. Les droits de republication publique ou commerciale restent à confirmer.[1][4]

Le lien Catbox fourni renvoyait HTTP 200 avec un corps de zéro octet. Aucune image de cette URL n’a pu être examinée ou incorporée. Le schéma affiché en slide 8 est celui du cadrage original local du projet, recadré pour sa lisibilité. Son identité avec le fichier Catbox n’est pas affirmée.[3]

Hermes Agent est présenté à partir de la documentation officielle. Son intégration à KuberWeaver est un scénario futur ; il n’est pas actuellement connecté comme fournisseur de diagnostic.[2]

Le PDF et le PowerPoint sont deux exports du même code de mise en page. Le PDF n’est pas une conversion LibreOffice du PowerPoint. Le texte du PowerPoint est éditable ; les deux illustrations sont intégrées comme images. Les 14 pages du PDF ont été rendues et inspectées visuellement. Les limites des blocs de texte et les éléments du PowerPoint ont été contrôlés séparément.

## Sources

[1] https://platformengineering.org/blog/what-is-infrastructure-platform-engineering
[2] https://hermes-agent.nousresearch.com/docs
[3] https://files.catbox.moe/cwo3fv.jpeg
[4] https://cdn.prod.website-files.com/6489e23dd070ba71d41a33b2/67b73877af69377f1ad55b9a_AD_4nXeSisWft9MHyHrKbXH-_MePMav-wP-J6mJh6hURVvao_Na39loV3kwHS6fDC-L0pmb4VczhjstgXNlDMYyUSFkFhD1U8p11X-9XInGZQ98tEaVlGg2NktMquYKXDeIfYFuAEIEg.avif
