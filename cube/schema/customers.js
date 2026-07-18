cube(`customers`, {
  sql: `SELECT * FROM customers`,

  measures: {
    count: {
      type: `count`
    }
  },

  dimensions: {
    id: {
      sql: `id`,
      type: `number`,
      primaryKey: true
    },
    name: {
      sql: `name`,
      type: `string`
    },
    email: {
      sql: `email`,
      type: `string`
    },
    country: {
      sql: `country`,
      type: `string`
    }
  }
});
