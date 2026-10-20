require 'json'
payload = JSON.parse(ENV.fetch("NEXTSEEK_PAYLOAD"))

admin = User.joins(:person).detect { |u| u.person&.is_admin? }
raise 'No SEEK admin user available for rails runner' if admin.nil?
User.with_current_user(admin) do

  user = User.find(payload['user_id'])
  person = user.person
  raise 'User has no linked person' if person.nil?

  if payload['first_name']
    person.first_name = payload['first_name']
  end
  if payload['last_name']
    person.last_name = payload['last_name']
  end
  if payload['email']
    person.email = payload['email']
  end
  if payload['first_name'] || payload['last_name'] || payload['email']
    person.save!
  end

  if payload['password'] && payload['password_confirmation']
    user.password = payload['password']
    user.password_confirmation = payload['password_confirmation']
    user.save!
  end

  if payload.key?('active')
    if payload['active']
      user.activate unless user.active?
    elsif user.active?
      user.send(:make_activation_code)
      user.save(validate: false)
    end
  end

  if payload['project_id'] && payload['institution_id']
    project = Project.find(payload['project_id'])
    institution = Institution.find(payload['institution_id'])
    person.add_to_project_and_institution(project, institution)
  end

  wg = person.current_work_groups.first
  project_id = wg&.project_id
  institution_id = wg&.institution_id

  puts({
    ok: true,
    user_id: user.id,
    person_id: person.id,
    login: user.login,
    email: person.email,
    active: user.active?,
    project_id: project_id,
    institution_id: institution_id
  }.to_json)
rescue => e
  puts({ ok: false, error: e.message, class: e.class.name }.to_json)
  exit 1

end
