frappe.provide("erpnext.stock");

const CHART_COLORS = {
	red: 'rgb(255, 99, 132)',
	yellow: 'rgb(255, 205, 86)',
	blue: 'rgb(54, 162, 235)',
	orange: 'rgb(255, 159, 64)',
	green: 'rgb(75, 192, 192)',
	purple: 'rgb(153, 102, 255)',
	grey: 'rgb(201, 203, 207)'
};
erpnext.stock.ItemDashboard = class ItemDashboard {
	constructor(opts) {
		$.extend(this, opts);
		this.make();
	}
	make() {
		var me = this;
		this.start = 0;
		if (!this.sort_by) {
			this.sort_by = "projected_qty";
			this.sort_order = "asc";
		}

		this.content = $(frappe.render_template("item_dashboard")).appendTo(this.parent);
		this.result = this.content.find(".result");

		this.content.on("click", ".btn-move", function () {
			handle_move_add($(this), "Move");
		});

		this.content.on("click", ".btn-add", function () {
			handle_move_add($(this), "Add");
		});

		this.content.on('click', '.btn-transfer', function () {
			handle_stock($(this), "transfer");
		})

		this.content.on('click', '.btn-send-stock', function () {
			handle_stock($(this), "pull");
		})
		
		this.content.on('click', '.btn-edit', function () {
			let item = unescape($(this).attr('data-item'));
			let warehouse = unescape($(this).attr('data-warehouse'));
			let company = unescape($(this).attr('data-company'));
			frappe.db.get_value('Putaway Rule', {
				'item_code': item,
				'warehouse': warehouse,
				'company': company
			}, 'name', (r) => {
				frappe.set_route("Form", "Putaway Rule", r.name);
			});
		});

		this.content.on('click', '.pending-breakdown', function () {
			let item_code = unescape($(this).attr('data-item'));
			let warehouse = unescape($(this).attr('data-warehouse'));

			if ($(`#pending-breakdown-${item_code}`).length) {
				toggle_display(item_code, true);
			} else {
				let poi_results = get_coming_stocks(item_code, warehouse);
				let boi_results = get_reserved_stocks(item_code);
				append_breakdown($(this), item_code, poi_results, boi_results);
			}
		});

		this.content.on('click', '.to-collapse', function () {
			let item = unescape($(this).attr('data-item'));
			// let warehouse = unescape($(this).attr('data-warehouse'));
			// handle_breakdown($(this), item, warehouse);

			if (document.getElementById(`pending-breakdown-${item}`)) {
				toggle_display(item, false);
			} else {
				frappe.show_alert("Nothing to collapse")
			}
		});

		function toggle_display(item, display) {
			let element = $(document.getElementById(`pending-breakdown-${item}`))
			if (display) {
				element.removeClass('hide');
			} else {
				element.addClass('hide');
			}
		}


		function handle_move_add(element, action) {
			let item = unescape(element.attr("data-item"));
			let warehouse = unescape(element.attr("data-warehouse"));
			let actual_qty = unescape(element.attr("data-actual_qty"));
			let disable_quick_entry = Number(unescape(element.attr("data-disable_quick_entry")));
			let entry_type = action === "Move" ? "Material Transfer" : "Material Receipt";
			let stock_uom = unescape(element.attr("data-stock-uom"));

			if (disable_quick_entry) {
				open_stock_entry(item, warehouse, entry_type);
			} else {
				if (action === "Add") {
					let rate = unescape($(this).attr("data-rate"));
					erpnext.stock.move_item(item, null, warehouse, actual_qty, rate, stock_uom, function () {
						me.refresh();
					});
				} else {
					erpnext.stock.move_item(item, warehouse, null, actual_qty, null, stock_uom, function () {
						me.refresh();
					});
				}
			}
		}

		function handle_stock(element, action) {
			let item = unescape(element.attr('data-item'));
			let current_site_qty = Number(unescape(element.attr('data-current_site_qty')));
			let target_site = unescape(element.attr('data-target_site'));
			let current_site = unescape(element.attr('data-current_site'));
			let has_batch_no = Number(unescape(element.attr('data-has_batch_no')));
			let price_data = [];
			let tar_batch_data = [];
			let cur_batch_data = [];
			
			frappe.call({
				method: 'fxnmrnth.utils.stock_receiver.fetch_item_price',
				args: {
					params: {
						method: 'item_price',
						action: action,
						item_code: item,
						target_site: target_site,
						current_site: current_site,
						user_email: frappe.session.user_email,
						user_name: frappe.session.user_fullname
					}
				},
				async: false,
				callback: function (r) {
					if (r.message.message) {
						price_data = r.message.message;
					}
				}
			});
		
			if (price_data && price_data.length > 0) {
				let price_message = price_data.map(data => 
					`<li>${data.message}</li>`
				).join('');
		
				let dialog = new frappe.ui.Dialog({
					title: 'Intersite Price Discrepancies Found',
					fields: [
						{
							label: 'Price Discrepancies',
							fieldname: 'price_discrepancies',
							fieldtype: 'HTML',
							options: `
								<div style="padding: 10px; max-height: 200px; overflow-y: auto;">
									<p style="margin-bottom: 10px;">The following price discrepancies have been found between the <b>${current_site}</b> and the <b>${target_site}</b> for ${item}. Please review them carefully before continuing.</p>
									<br>
									<ul style="list-style-type: disc; padding-left: 20px;">
										${price_message}
									</ul>
								</div>
							`
						}
					],
					primary_action_label: 'Continue',
					primary_action(values) {
						dialog.hide();
						proceed_with_transfer(action);	
					},
					secondary_action_label: "Close"
				});
		
				dialog.show();
		
				dialog.set_secondary_action(() => {
					dialog.hide();
				});
		
				return; 
			}

			proceed_with_transfer(action);	
			
			function proceed_with_transfer(action) {
				frappe.call({
					method: 'fxnmrnth.utils.stock_receiver.fetch_batch_data',
					args: {
						params: {
							method: 'batch_data',
							action: action,
							item_code: item,
							target_site: target_site,
							current_site: current_site,
							has_batch_no: has_batch_no, 
							user_email: frappe.session.user_email,
							user_name: frappe.session.user_fullname
						}
					},
					async: false,
					callback: function (r) {
						if (r.message) {
							cur_batch_data = r.message.current_batch_data;
							tar_batch_data = r.message.target_batch_data;
						}
					}
				});

				stock_transfer_dialog(item, cur_batch_data, tar_batch_data, current_site, target_site, current_site_qty, has_batch_no, action, function (values) {
					create_stock_entry(item, current_site, target_site, values, action, has_batch_no);
					me.refresh();
				});
			}
		}

		function open_stock_entry(item, warehouse, entry_type) {
			frappe.model.with_doctype("Stock Entry", function () {
				var doc = frappe.model.get_new_doc("Stock Entry");
				if (entry_type) {
					doc.stock_entry_type = entry_type;
				}

				var row = frappe.model.add_child(doc, "items");
				row.item_code = item;

				if (entry_type === "Material Transfer") {
					row.s_warehouse = warehouse;
				} else {
					row.t_warehouse = warehouse;
				}

				frappe.set_route("Form", doc.doctype, doc.name);
			});
		}

		// more
		this.content.find(".btn-more").on("click", function () {
			me.start += me.page_length;
			me.refresh();
		});
	}
	refresh() {
		if (this.before_refresh) {
			this.before_refresh();
		}

		let args = {
			item_code: this.item_code,
			warehouse: this.warehouse,
			parent_warehouse: this.parent_warehouse,
			item_group: this.item_group,
			company: this.company,
			start: this.start,
			sort_by: this.sort_by,
			sort_order: this.sort_order,
			brand: this.brand,
			limit_page_length: this.limit_page_length
		};

		var me = this;
		frappe.call({
			method: this.method,
			args: args,
			callback: function (r) {
				me.render(r.message);
				if (me.after_refresh) {
					me.after_refresh();
				}
			},
		});
	}
	render(data) {
		if (this.start === 0) {
			this.max_count = 0;
			this.result.empty();
		}

		let context = "";

		if (this.page_name === "warehouse-capacity-summary") {
			context = this.get_capacity_dashboard_data(data);
		} else {
			context = this.get_item_dashboard_data(data, this.max_count, true);
		}

		// show more button
		if (data && data.length === this.page_length + 1) {
			this.content.find(".more").removeClass("hidden");

			// remove the last element
			data.splice(-1);
		} else {
			this.content.find(".more").addClass("hidden");
		}

		// If not any stock in any warehouses provide a message to end user
		if (context.data.length > 0) {
			this.content.find(".result").css("text-align", "unset");
			$(frappe.render_template(this.template, context)).appendTo(this.result);
		} else {
			var message = __("No Stock Available Currently");
			this.content.find(".result").css("text-align", "center");

			$(`<div class='text-muted' style='margin: 20px 5px;'>
				${message} </div>`).appendTo(this.result);
		}
	}

	get_item_dashboard_data(data, max_count, show_item) {
		if (!max_count) max_count = 0;
		if (!data) data = [];

		data.forEach(function (d) {
			d.actual_or_pending =
				d.projected_qty +
				d.reserved_qty +
				d.reserved_qty_for_production +
				d.reserved_qty_for_sub_contract;
			d.pending_qty = 0;
			d.total_reserved =
				d.reserved_qty + d.reserved_qty_for_production + d.reserved_qty_for_sub_contract;
			if (d.actual_or_pending > d.actual_qty) {
				d.pending_qty = d.actual_or_pending - d.actual_qty;
			}

			max_count = Math.max(d.actual_or_pending, d.actual_qty, d.total_reserved, max_count);
		});

		let can_write = 0;
		if (frappe.boot.user.can_write.indexOf("Stock Entry") >= 0) {
			can_write = 1;
		}

		return {
			data: data,
			max_count: max_count,
			can_write: can_write,
			show_item: show_item || false,
		};
	}

	get_capacity_dashboard_data(data) {
		if (!data) data = [];

		data.forEach(function (d) {
			d.color = d.percent_occupied >= 80 ? "#f8814f" : "#2490ef";
		});

		let can_write = 0;
		if (frappe.boot.user.can_write.indexOf("Putaway Rule") >= 0) {
			can_write = 1;
		}

		return {
			data: data,
			can_write: can_write,
		};
	}
};

erpnext.stock.move_item = function (item, source, target, actual_qty, rate, stock_uom, callback) {
	var dialog = new frappe.ui.Dialog({
		title: target ? __("Add Item") : __("Move Item"),
		fields: [
			{
				fieldname: "item_code",
				label: __("Item"),
				fieldtype: "Link",
				options: "Item",
				read_only: 1,
			},
			{
				fieldname: "source",
				label: __("Source Warehouse"),
				fieldtype: "Link",
				options: "Warehouse",
				read_only: 1,
			},
			{
				fieldname: "target",
				label: __("Target Warehouse"),
				fieldtype: "Link",
				options: "Warehouse",
				reqd: 1,
				get_query() {
					return {
						filters: {
							is_group: 0,
						},
					};
				},
			},
			{
				fieldname: "qty",
				label: __("Quantity"),
				reqd: 1,
				fieldtype: "Float",
				description: __("Available {0}", [actual_qty]),
			},
			{
				fieldname: "rate",
				label: __("Rate"),
				fieldtype: "Currency",
				hidden: 1,
			},
		],
	});
	dialog.show();
	dialog.get_field("item_code").set_input(item);

	if (source) {
		dialog.get_field("source").set_input(source);
	} else {
		dialog.get_field("source").df.hidden = 1;
		dialog.get_field("source").refresh();
	}

	if (rate) {
		dialog.get_field("rate").set_value(rate);
		dialog.get_field("rate").df.hidden = 0;
		dialog.get_field("rate").refresh();
	}

	if (target) {
		dialog.get_field("target").df.read_only = 1;
		dialog.get_field("target").value = target;
		dialog.get_field("target").refresh();
	}

	dialog.set_primary_action(__("Create Stock Entry"), function () {
		if (source && (dialog.get_value("qty") == 0 || dialog.get_value("qty") > actual_qty)) {
			frappe.msgprint(__("Quantity must be greater than zero, and less or equal to {0}", [actual_qty]));
			return;
		}

		if (dialog.get_value("source") === dialog.get_value("target")) {
			frappe.msgprint(__("Source and target warehouse must be different"));
			return;
		}

		frappe.model.with_doctype("Stock Entry", function () {
			let doc = frappe.model.get_new_doc("Stock Entry");
			doc.from_warehouse = dialog.get_value("source");
			doc.to_warehouse = dialog.get_value("target");
			doc.stock_entry_type = doc.from_warehouse ? "Material Transfer" : "Material Receipt";
			let row = frappe.model.add_child(doc, "items");
			row.item_code = dialog.get_value("item_code");
			row.s_warehouse = dialog.get_value("source");
			row.stock_uom = stock_uom;
			row.uom = stock_uom;
			row.t_warehouse = dialog.get_value("target");
			row.qty = dialog.get_value("qty");
			row.conversion_factor = 1;
			row.transfer_qty = dialog.get_value("qty");
			row.basic_rate = dialog.get_value("rate");
			frappe.set_route("Form", doc.doctype, doc.name);
		});
	});

	$('<p style="margin-left: 10px;"><a class="link-open text-muted small">' +
		__("Add more items or open full form") + '</a></p>')
		.appendTo(dialog.body)
		.find('.link-open')
		.on('click', function () {
			frappe.model.with_doctype('Stock Entry', function () {
				var doc = frappe.model.get_new_doc('Stock Entry');
				doc.from_warehouse = dialog.get_value('source');
				doc.to_warehouse = dialog.get_value('target');
				var row = frappe.model.add_child(doc, 'items');
				row.item_code = dialog.get_value('item_code');
				row.f_warehouse = dialog.get_value('target');
				row.t_warehouse = dialog.get_value('target');
				row.qty = dialog.get_value('qty');
				row.conversion_factor = 1;
				row.transfer_qty = dialog.get_value('qty');
				row.basic_rate = dialog.get_value('rate');
				frappe.set_route('Form', doc.doctype, doc.name);
			});
		});
}


function get_coming_stocks(item_code, warehouse) {
	let result = "";
	frappe.call({
		method: "fxnmrnth.fxnmrnth.report.backorder_summary.backorder_summary.get_qty",
		args: {
			item_code: item_code,
			warehouse: warehouse,
		},
		async: false,
		callback: (r) => {
			if (!r.exc) {
				result = r.message;
			} else {
				frappe.show_alert(`Error happened: ${r.exc}`)
			}
		}
	})
	return result
}

function get_reserved_stocks(item_code, warehouse) {
	let result = "";
	frappe.call({
		method: "fxnmrnth.fxnmrnth.report.backorder_analytics.backorder_analytics.get_bo_qty",
		args: {
			item_code: item_code,
		},
		async: false,
		callback: (r) => {
			if (!r.exc) {
				result = r.message;
			} else {
				frappe.show_alert(`Error happened: ${r.exc}`)
			}
		}
	})
	return result
}

function append_breakdown(element, item_code, poi_results, boi_results) {
	const $row = $(element).parents(`div[parent="${item_code}"]`)

	// Parse poi_results
	const poi_number_data = poi_results.map(r => (r.qty - r.received_qty))
	const poi_total_number = poi_results.reduce((total, r) => {
		return total + (r.qty - r.received_qty)
	}, 0)
	const poi_label_data = poi_results.map(r => `${r.parent} | ETA:${r.schedule_date}`)
	const poi_backgroundColor = []
	for (let i = 0; i < poi_results.length; i++) {
		const colorIndex = i % Object.keys(CHART_COLORS).length;
		poi_backgroundColor.push(Object.values(CHART_COLORS)[colorIndex])
	}

	// Setup poi chart
	let po_dataset_label = "Purchase Order"
	let po_options = {
		responsive: true,
		maintainAspectRatio: false,
		plugins: {
			legend: {
				position: "bottom",
				display: (poi_results.length >= 10) ? false : true,
				align: "start",

			},
			title: {
				display: true,
				text: `Total ${poi_total_number} ${po_dataset_label} Breakdown`
			}
		},
		// events: ['mousemove', 'mouseout', 'click'],
	}
	const po_config = setup(poi_number_data, poi_label_data, poi_backgroundColor, po_dataset_label, po_options);

	// Parse boi_results
	const boi_number_data = boi_results.map(r => r.qty)

	// const boi_label_data = boi_results.map( r=> `${r.parent} | ${(r.customer_name)? (r.customer_name): "Customer Name Unset!"} | ${r.added_time} | Stock Required: {}`)

	const boi_total_number = boi_results.reduce((total, r) => {
		return total + r.qty
	}, 0)
	const boi_backgroundColor = []
	const boi_label_data = []
	var stockRequired = 0
	for (let i = 0; i < boi_results.length; i++) {
		const colorIndex = (i + 5) % Object.keys(CHART_COLORS).length;
		boi_backgroundColor.push(Object.values(CHART_COLORS)[colorIndex])

		stockRequired += boi_results[i].qty

		let message = [`${boi_results[i].parent}`]
		message.push(`${(boi_results[i].customer_name) ? (boi_results[i].customer_name) : "Customer Name Unset!"}`)
		message.push(`${boi_results[i].added_time}`)
		message.push(`Stock Required: ${stockRequired}`)
		message.push(`BO Qty ${boi_results[i].qty}`)
		message.push(`Customer ID: ${boi_results[i].customer_id}`)
		message.push(`Expected Arrival Time: ${boi_results[i].expected_arrival_time}`)
		boi_label_data.push(message)

	}


	// Setup boi chart
	let bo_dataset_label = "Backorder"
	let bo_options = {
		responsive: true,
		maintainAspectRatio: false,
		plugins: {
			legend: {
				position: "bottom",
				display: false,
				align: "start",
			},
			title: {
				display: true,
				text: `Total ${boi_total_number} ${bo_dataset_label} Breakdown`
			}
		},
	}
	const bo_config = setup(boi_number_data, boi_label_data, boi_backgroundColor, bo_dataset_label, bo_options);

	let Button = ""
	if (boi_results.length != 0) {
		Button = `<div style="text-align: center">
		<button id="backorder-report" class="btn btn-primary" style="margin-top:20px">Open Backorder Analytics for this item.</button> 
		</div>`
	}
	// Add div for canvas
	let chartDisplay = `<div id="pending-breakdown-${item_code}" width="100%" class="col-sm-12" style="padding: 15px 15px;
		border-top: 1px solid #d1d8dd; border-left: 6px solid deepskyblue;">
		<div class="col-sm-6" style="width:48%; display:inline-block;">
			<canvas id="bo-${item_code}"></canvas>
		</div>
		<div class="col-sm-6" style="width:48%; display:inline-block;">
			<canvas id="po-${item_code}"></canvas>
		</div>
		${Button}

	</div>`

	let empty_display = `<div id="pending-breakdown-${item_code}" width="100%" class="col-sm-12";
		border-top: 1px solid #d1d8dd; border-left: 2px solid deepskyblue;">
		<h4 style="text-align: center;padding: 10px;"> No reserved/incoming stock to display. </h4>
	</div>`

	if (poi_results.length > 0 || boi_results.length > 0) {
		if (!document.getElementById(`pending-breakdown-${item_code}`)) {
			$row.append(chartDisplay)
		}
	} else {
		if (!document.getElementById(`pending-breakdown-${item_code}`)) {
			$row.append(empty_display)
		}

	}
	$("#backorder-report").click(function () {
		frappe.set_route("query-report", "Backorder Analytics", { "item_code": item_code })
	});
	// Render the chart using our configuration
	$.getScript("https://cdn.jsdelivr.net/npm/chart.js").done(function () {
		if (poi_results.length > 0) {
			let po_ctx = document.getElementById(`po-${item_code}`);
			let poChart = new Chart(po_ctx, po_config);

			// Open a new tab for purchase order review
			po_ctx.onclick = function (evt) {
				var activePoints = poChart.getActiveElements(evt);
				if (activePoints.length > 0) {
					//get the internal index of slice in pie chart
					var clickedElementindex = activePoints[0]["index"];

					//get specific label by index 
					var label = poChart.data.labels[clickedElementindex];
					let po_id = label.split(" | ")[0]
					window.open(frappe.urllib.get_full_url(`/app/purchase-order/${po_id}`));

					//get value by index	  
					var value = poChart.data.datasets[0].data[clickedElementindex];
				}
			}
		}

		if (boi_results.length > 0) {
			let bo_ctx = document.getElementById(`bo-${item_code}`);
			let boChart = new Chart(
				bo_ctx,
				bo_config
			);

			// Open a new tab for backorder review
			bo_ctx.onclick = function (evt) {
				var activePoints = boChart.getActiveElements(evt);
				if (activePoints.length > 0) {
					//get the internal index of slice in pie chart
					var clickedElementindex = activePoints[0]["index"];

					//get specific label by index 
					var label = boChart.data.labels[clickedElementindex];
					let bo_id = label[0]
					window.open(frappe.urllib.get_full_url(`/app/backorder/${bo_id}`));

					//get value by index	  
					var value = boChart.data.datasets[0].data[clickedElementindex];
				}
			}
		}
	});
}

// Setup for Purchase Order Item chart
function setup(number_data, label_data, backgroundColor, dataset_label, options) {
	const data = {
		labels: label_data,
		datasets: [{
			label: dataset_label,
			data: number_data,
			backgroundColor: backgroundColor,
			hoverOffset: 4
		}]
	};
	const config = {
		type: 'doughnut',
		data,
		options: options,
	};
	return config
}


function stock_transfer_dialog(item_code, cur_batch_data, tar_batch_data, current_site, target_site, actual_qty, has_batch_no, action,callback) {
	let batchLabel;
	let batchReqd;
	let batchDesc;

	if (action == "transfer") {
		batchLabel = "From Batch"
		batchReqd = 1;
		batchDesc = `
		The top table contains the batches that are currently in <b>${target_site}</b>. Use the select button and the quantity field to transfer stock directly into that batch.
		<br><br>
		Alternatively, use the "Add Row" button and enter the item code, quantity, and expiry date to create a new batch in <b>${target_site}</b>. The batch name will be automatically created using the item code with the expiry date.`
	} else {
		batchLabel = "To Batch"
		batchReqd = 0;

		batchDesc = `
			The top table contains the batches that are currently in <b>${target_site}</b>. Use the select button and the quantity field to take stock directly from that batch and set the expiry date for the batch in <b>${current_site}</b>.
			<br><br>
			This tool will automatically look up batches in <b>${current_site}</b> to assign the qty to. A new one will be created if a batch with the same expiration date cannot be found.
		`
	}

	let d = new frappe.ui.Dialog({
		title: 'Stock Handler',
		fields: [
			{
				fieldname: "tar_batch_data",
				fieldtype: "HTML"
			},
			{
				fieldname: "cur_batch_data",
				fieldtype: "HTML",
				hidden: !batchReqd
			},
			{
				fieldname: 'stock_items',
				label: 'Items',
				fieldtype: 'Table',
				in_place_edit: true,
				cannot_add_rows: !batchReqd,
				data: [],
				fields: [
					{
						fieldname: "batch_no",
						fieldtype: "Data",
						label: "Batch Name or Item Code",
						reqd: 1,
						read_only: 1,
						in_list_view: 1
					},
					{
						fieldname: 'qty',
						label: 'Quantity',
						fieldtype: 'Int',
						in_list_view: 1,
						reqd: 1
					},
					{
						fieldname: 'date',
						label: 'Expiry Date',
						fieldtype: 'Date',
						in_list_view: 1,
						reqd: 1,
						read_only: !batchReqd
					},
					{
						fieldname: "from_batch",
						label: batchLabel,
						fieldtype: "Link",
						options: "Batch",
						in_list_view: batchReqd,
						reqd: batchReqd,
						get_query: function (doc, cdt, cdn) {
							let grid = d.fields_dict['stock_items'].grid;
							let row = grid.get_row(cdn);
							let expiry_date = row ? row.doc.date : null;
							
							if (!expiry_date) {
								return {
									filters: {
										item: item_code,
										batch_qty: 1
									}
								};
							}
	
							return {
								filters: {
									item: item_code,
									batch_qty: 1,
									expiry_date: expiry_date
								}
							};
						}
					}
				]
			}
		],
		size: "large",
		"static": true,
		primary_action_label: 'Submit',
		primary_action: function (values) {
			if (!values || !values.stock_items) {
				d.hide();
				return;
			}

			let submit_button = d.get_primary_btn();
			submit_button.prop("disabled", true);

			let stockItems = values.stock_items.filter(item => item.qty > 0 && item.qty != undefined && item.qty != '');
			let rowsValid;

			if (batchReqd) {
				rowsValid = stockItems.every(item => item.date != undefined && item.date != null && item.date.trim() != '' && item.from_batch != undefined && item.from_batch != null && item.from_batch.trim() != '');
			} else {
				rowsValid = stockItems.every(item => {
					const isDateValid = item.date !== undefined && item.date !== null && item.date.trim() !== '';
					const isQtyValid = item.qty <= item.batch_qty;
					return isDateValid && isQtyValid;
				});
			}

			let totalQty = stockItems.reduce((sum, item) => sum + item.qty, 0);

			if (!rowsValid) {
				if (batchReqd) {
					frappe.msgprint(__('Please ensure that each row has an expiry date and batch number set.'));
					submit_button.prop("disabled", false);
				} else {
					frappe.msgprint(__('Please ensure that each row has an expiration date and that the selected quantity is less than the available quantity of batches.'));
					submit_button.prop("disabled", false);
				}
				
				return;
			}

			if (batchReqd && totalQty > actual_qty) {
				frappe.msgprint(__(`There are currently ${actual_qty} units available in <b>${current_site}</b>, and you are trying to transfer ${totalQty}.<br><br>Please reduce the qty of one or more lines.`));
				submit_button.prop("disabled", false);
				return;
			}

			if (typeof callback === 'function') {
				if (stockItems && stockItems.length == 0) {
					submit_button.prop("disabled", false);
					frappe.throw(`Cancelling as the batch data did not populate correctly.<br><br><b>Please ensure all fields have been entered correctly.</b>`)
				} else {
					callback(stockItems);
				}
			}
			d.hide();
			return;
		},
		secondary_action_label: 'Cancel',
		secondary_action: function () {
			d.hide();
			return;
		}
	});

	let tarBatchHtml = `<h5 style="text-align: center;"><br>There are currently no batches with stock for ${item_code} in ${target_site}.<br><br></h5>`;

	if (tar_batch_data && tar_batch_data.length > 0) {
		let filtered_batches = tar_batch_data;

		if (action === "pull") {
			filtered_batches = tar_batch_data.filter(batch => batch.batch_qty > 0);
		}

		if (filtered_batches.length > 0) {
			tarBatchHtml = `
				<h4 style="text-align: center;">${target_site} Batches</h4>
				<div id="dialog-description" style="margin-top: 20px; text-align: center;">
					${batchDesc}
				</div>
				<table class="table table-bordered table-striped">
					<thead>
						<tr>
							<th class="col-md-4">Batch Name (${target_site})</th>
							<th class="col-md-2" style="text-align: center;">Batch Qty</th>
							<th class="col-md-2" style="text-align: center;">Batch Expiry</th>
							<th class="col-md-1" style="text-align: center;"></th>
						</tr>
					</thead>
					<tbody>
						${filtered_batches.map(batch => `
							<tr>
								<td>${batch.batch_id}</td>
								<td style="text-align: center;">${batch.batch_qty}</td>
								<td style="color: ${batch.is_shortdated ? 'green' : 'orange'}; text-align: center;">
									${batch.expiry_date ? batch.expiry_date : 'No Expiry'}
								</td>
								<td style="text-align: center;"><button class="btn btn-xs btn-secondary select-batch" data-batch-id="${batch.batch_id}" data-expiry-date="${batch.expiry_date}" data-batch-qty="${batch.batch_qty}">Select</button></td>
							</tr>
						`).join('')}
					</tbody>
				</table>
			`;
		}
	}

	let curBatchHtml = `<h5 style="text-align: center;"><br>There are currently no batches with stock for ${item_code} in ${current_site}.<br><br></h5>`;

	if (action == 'transfer' && (cur_batch_data && cur_batch_data.length > 0)) {
		let cur_filtered_batches = cur_batch_data;
		cur_filtered_batches = cur_batch_data.filter(batch => batch.batch_qty > 0);

		if (cur_filtered_batches.length > 0) {
			curBatchHtml = `
				<h4 style="text-align: center;">${current_site} Batches</h4>
				<div id="dialog-description" style="margin-top: 20px; text-align: center;">
					This table contains the batches currently in <b>${current_site}</b> with a quantity greater than 0.
				</div>
				<table class="table table-bordered table-striped">
					<thead>
						<tr>
							<th class="col-md-4">Batch Name (${current_site})</th>
							<th class="col-md-2" style="text-align: center;">Batch Qty</th>
							<th class="col-md-2" style="text-align: center;">Batch Expiry</th>
						</tr>
					</thead>
					<tbody>
						${cur_filtered_batches.map(batch => `
							<tr>
								<td>${batch.batch_id}</td>
								<td style="text-align: center;">${batch.batch_qty}</td>
								<td style="color: ${batch.is_shortdated ? 'green' : 'orange'}; text-align: center;">
									${batch.expiry_date ? batch.expiry_date : 'No Expiry'}
								</td>
							</tr>
						`).join('')}
					</tbody>
				</table>
			`;
		}
	}
	
	d.fields_dict.tar_batch_data.$wrapper.html(tarBatchHtml);

	if (action == 'transfer') {
		d.fields_dict.cur_batch_data.$wrapper.html(curBatchHtml);
	}
	
	d.fields_dict.tar_batch_data.$wrapper.on('click', 'button.select-batch', function () {
		let batch_id = $(this).data('batch-id');
		let expiry_date = $(this).data('expiry-date');
		let batch_qty = $(this).data('batch-qty');
		let selectedBatch = tar_batch_data.find(batch => batch.batch_id == batch_id);

		if (selectedBatch) {
			let grid = d.fields_dict['stock_items'].grid;
			let isExpiryDatePresent = grid.df.data.some(row => row.date == expiry_date);

			if (!isExpiryDatePresent) {
				grid.df.data.push({
					batch_no: selectedBatch.batch_id,
					qty: 0,
					date: selectedBatch.expiry_date,
					batch_qty: batch_qty
				});
				grid.refresh();
				$(grid.wrapper).find('.grid-row-index').hide();
				$(grid.wrapper).find('.data-row .grid-row-index').hide();
				$(grid.wrapper).find('.row-index.sortable-handle.col.col-xs-1').css('pointer-events', 'none');
				$(grid.wrapper).find('.row-index.sortable-handle.col.col-xs-1 input.grid-row-check').css('pointer-events', 'all');
			} else {
				frappe.msgprint(__('This expiry date is already in the items table.'));
			}
		}
	});

	d.show();

	d.$wrapper.find('.grid-add-row').on('click', function () {
		console.log('click event')
		let grid = d.fields_dict['stock_items'].grid;
		grid.df.data = grid.df.data.filter(row => !(row.batch_no == undefined && row.date == undefined));

		grid.df.data.push({
			batch_no: item_code,
			qty: 0,
			date: ''
		});

		grid.refresh();
		$(grid.wrapper).find('.grid-row-index').hide();
		$(grid.wrapper).find('.data-row .grid-row-index').hide();
		$(grid.wrapper).find('.row-index.sortable-handle.col.col-xs-1').css('pointer-events', 'none');
		$(grid.wrapper).find('.row-index.sortable-handle.col.col-xs-1 input.grid-row-check').css('pointer-events', 'all');
	});
}

function create_stock_entry(item_code, current_site, target_site, values, action, has_batch_no) {
	// Used to store reference of source and target site stock entries
	function generateRandomHash(length) {
        const characters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
        let hash = '';
        for (let i = 0; i < length; i++) {
            hash += characters.charAt(Math.floor(Math.random() * characters.length));
        }
        return hash;
    }

    let intersite_key = generateRandomHash(16);

	frappe.call({
		method: 'fxnmrnth.utils.stock_receiver.validate_create_stock_transfer',
		args: {
			params: {
				method: 'validate_and_create',
				action: action,
				item_code: item_code,
				target_site: target_site,
				current_site: current_site,
				has_batch_no: has_batch_no,
				batch_data: values,
				intersite_key: intersite_key,
				user_email: frappe.session.user_email,
				user_name: frappe.session.user_fullname
			}
		},
		async: false,
		callback: function (r) {
			if (r.message) {
				let entry_1 = `https://${r.message.current_site_url}/app/stock-entry/${r.message.transfer_entry}`;
				let entry_2 = `https://${r.message.target_site_url}/app/stock-entry/${r.message.receipt_entry}`;
				
				let message = `
					<strong>Stock Transfer Successful!</strong><br><br>
					Material Issue (${r.message.transfer_site}):<br> <a href="${entry_1}" target="_blank">${r.message.transfer_entry}</a>
					<br><br>
					Material Receipt (${r.message.receipt_site}):<br> <a href="${entry_2}" target="_blank">${r.message.receipt_entry}</a>
				`;

				frappe.show_alert({message: message, indicator: 'green'}, 15);
			}
		}
	})
}
