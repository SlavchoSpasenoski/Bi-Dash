# Classicmodels data source context for Bi-Dash

Prepared on 2026-11-16 from the local `mysqlsampledatabase.sql` file. The SQL file was inspected as source data; it was not executed or imported. This context has not been saved to the active Bi-Dash settings.

## Text to copy

The data source is the MySQL database `classicmodels`, a sample database for a company selling collectible scale models of cars, motorcycles, aircraft, ships, trains, trucks and buses. Use the exact English table and column names in SQL. Write explanations and chart titles in English unless the user requests another language.

### Tables and grain

- `customers`: one row per customer; primary key `customerNumber`. Includes `customerName`, contact and address details, `country`, `city`, `creditLimit` and nullable `salesRepEmployeeNumber`.
- `orders`: one row per order; primary key `orderNumber`. Includes `customerNumber`, `orderDate`, `requiredDate`, nullable `shippedDate`, `status` and `comments`.
- `orderdetails`: one row per product within an order; composite primary key (`orderNumber`, `productCode`). Includes `quantityOrdered`, `priceEach` and `orderLineNumber`. This is the main fact table for sales value and quantity.
- `products`: one row per product; primary key `productCode`. Includes `productName`, `productLine`, `productScale`, `productVendor`, `quantityInStock`, `buyPrice` and `MSRP`.
- `productlines`: one row per product category; primary key `productLine`. Includes text/HTML descriptions and an image. Use the category name for ordinary analysis.
- `payments`: one row per payment; composite primary key (`customerNumber`, `checkNumber`). Includes `paymentDate` and `amount`. Payments belong to customers and have no direct relationship to individual orders.
- `employees`: one row per employee; primary key `employeeNumber`. Includes `firstName`, `lastName`, `jobTitle`, `officeCode` and nullable `reportsTo`, identifying the manager.
- `offices`: one row per office; primary key `officeCode`. Includes `city`, `country`, `territory` and address details.

### Relationships

- `customers.customerNumber = orders.customerNumber` (one to many).
- `orders.orderNumber = orderdetails.orderNumber` (one to many).
- `products.productCode = orderdetails.productCode` (one to many).
- `productlines.productLine = products.productLine` (one to many).
- `customers.customerNumber = payments.customerNumber` (one to many).
- `employees.employeeNumber = customers.salesRepEmployeeNumber` (one to many).
- `offices.officeCode = employees.officeCode` (one to many).
- Join `employees` to itself using `manager.employeeNumber = employee.reportsTo`, with separate aliases for the manager and employee.

### Metric definitions

- Order value: `SUM(orderdetails.quantityOrdered * orderdetails.priceEach)`. This measures ordered merchandise value, not verified accounting revenue or cash collected.
- For an unspecified request for "sales", use this value with `orders.status = 'Shipped'` and clearly disclose the filter. Follow explicit requests for all orders or other statuses. This is a suggested reporting convention, not a business rule established by the schema.
- Units: `SUM(orderdetails.quantityOrdered)`, using the same status filter as the corresponding order value.
- Order count after joining order lines: `COUNT(DISTINCT orders.orderNumber)`.
- Average order value: selected order value divided by the distinct number of selected orders. Protect against division by zero.
- Cash collected: `SUM(payments.amount)`, analyzed using `payments.paymentDate`.
- Estimated merchandise cost: `SUM(orderdetails.quantityOrdered * products.buyPrice)`.
- Estimated gross profit: `SUM(orderdetails.quantityOrdered * (orderdetails.priceEach - products.buyPrice))`. Estimated gross margin percentage: estimated gross profit / order value * 100, with protection against division by zero. There is no price history for `buyPrice`, so these are estimates, not verified historical profit figures.
- Stock quantity: `products.quantityInStock`. Estimated inventory cost: `SUM(products.quantityInStock * products.buyPrice)`, calculated at product grain without duplication through order lines.
- `MSRP` is the suggested retail price. Use `orderdetails.priceEach` for the actual order-line selling price. `creditLimit` is a credit limit, not an outstanding balance.

### Rules for accurate analysis

Do not directly join `payments` to `orders`/`orderdetails` when summing amounts: multiple payments and multiple order lines will multiply rows and inflate totals. Aggregate payments and orders separately by customer or another appropriate shared reporting grain, then join the aggregates. Do not allocate payments to individual orders without additional business logic. Do not describe order value minus payments as a verified outstanding balance.

Use `orderDate` for sales/order trends, `shippedDate` for shipment trends and `paymentDate` for cash collection trends. Available order statuses are `Shipped`, `Cancelled`, `Resolved`, `On Hold`, `In Process` and `Disputed`. Do not assume they all represent completed sales. A NULL `shippedDate` means no shipment date is recorded. Calculate shipment delay using `DATEDIFF(shippedDate, requiredDate)` only when a shipment date exists; positive values indicate late shipment.

Orders in the supplied sample range from 2003-01-06 to 2005-05-31; payments range from 2003-01-16 to 2005-06-09. If no period is requested, use the full available period rather than automatically selecting the last 30 days relative to today. If explicitly requested recent dates contain no data, return an empty result and explain the historical coverage. The database has no explicit currency field: do not label amounts USD, EUR or MKD without confirmation. There are no separate records for taxes, shipping costs, invoices or returns.

Distinguish customer country (`customers.country`) from office country (`offices.country`). For sales by employee, follow the relationship through the customer. This identifies the customer's currently assigned sales representative, not a historically verified salesperson for each order. Use LEFT JOIN when customers without representatives or transactions must remain in the results.

Generate only SELECT queries compatible with MySQL and the available tables and columns. Use explicit columns and clear aliases. Do not invent fields, currencies or business rules. Suitable analyses include monthly sales, top products, sales by category/customer/country/representative/office, order statuses, cash collection, estimated margins and inventory.

## Source inspection notes

Static inspection of INSERT records found 7 product categories, 110 products, 7 offices, 23 employees, 122 customers, 326 orders, 2,996 order lines and 273 payments. These counts describe the supplied file, not a verified live database. Context does not grant access permissions. In the current Bi-Dash implementation, shared organization AI context is included for Admin users and excluded from Editor AI requests.
