cube(`orders`, {
  sql: `SELECT * FROM orders`,

  joins: {
    customers: {
      sql: `${CUBE}.customer_id = ${customers}.id`,
      relationship: `belongsTo`
    }
  },

  measures: {
    count: {
      type: `count`
    },
    total_amount: {
      sql: `amount`,
      type: `sum`
    }
  },

  dimensions: {
    id: {
      sql: `id`,
      type: `number`,
      primaryKey: true
    },
    status: {
      sql: `status`,
      type: `string`
    },
    order_date: {
      sql: `order_date`,
      type: `time`
    }
  }
});
