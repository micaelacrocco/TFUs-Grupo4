> Siendo que algunas de las acciones tienen una naturaleza asincrona y otras sincrona, seria viable implementar eventos solamente para la parte sincrona?

------------

Si, iría por eventos para el flujo de órdenes y llamadas sincrónicas para consultas y validaciones inmediatas (catálogo, saldo). Cada movimiento de dinero sigue siendo una transacción ACID dentro de Wallet. Las razones:
- Una orden de inversión es asincrónica por naturaleza (pasa por estados: pendiente, ejecutada o rechazada), y ya la modelan así con el 202 Accepted.
- Ya tienen implementado Queue-Based Load Leveling, que es básicamente esto. Pasar a SOA sería retroceder en disponibilidad.
- El log de eventos sirve para auditoría, algo valioso en una app financiera.

Si igual prefieren SOA, es defendible, pero con otros argumentos: un modelo más simple de razonar, consistencia inmediata y orquestación centralizada. "No se pierden transacciones" no sirve, porque no es una ventaja real de SOA.